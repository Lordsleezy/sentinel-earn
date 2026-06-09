"""Orchestrates GitHub + HackerOne scanning and patch queue."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from sentinel_earn import db
from sentinel_earn import github_scanner
from sentinel_earn import queue_manager as qm
from sentinel_earn.config import load_settings
from sentinel_earn.executor import run_executor, execute_submit
from sentinel_earn.hackerone_discovery import discover_programs

logger = logging.getLogger("sentinel_earn.pipeline")

_worker_thread: Optional[threading.Thread] = None
_worker_running = False


def scan_github_bounties(max_issues: int = 20) -> Dict[str, Any]:
    db.init_db()
    issues = github_scanner.find_bounty_issues(max=max_issues)
    inserted = 0
    for issue in issues:
        result = github_scanner.queue_repair(issue)
        if result.get("opportunity_id"):
            inserted += 1
    return {"found": len(issues), "queued": inserted, "issues": issues[:10]}


def scan_hackerone_programs(limit: int = 50, force_refresh: bool = False) -> Dict[str, Any]:
    programs, meta = discover_programs(limit=limit, force_refresh=force_refresh, refresh=force_refresh)
    return {"programs": programs, "meta": meta.to_dict() if hasattr(meta, "to_dict") else meta}


def run_full_cycle() -> Dict[str, Any]:
    github = scan_github_bounties()
    hackerone = scan_hackerone_programs(limit=30)
    cycle = github_scanner.run_pipeline_cycle()
    return {
        "github": github,
        "hackerone_count": len(hackerone.get("programs", [])),
        "pipeline": cycle,
        "status": github_scanner.get_pipeline_status(),
    }


def get_dashboard() -> Dict[str, Any]:
    db.init_db()
    qm.initialize_queue()
    return {
        "opportunities": db.list_opportunities(limit=100),
        "submissions": db.list_submissions(),
        "earnings": db.get_earnings_summary(),
        "queue": qm.get_queue_stats(),
        "pipeline": github_scanner.get_pipeline_status(),
        "patches": list_patch_queue(),
    }


def list_patch_queue() -> List[Dict[str, Any]]:
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT p.*, o.title, o.issue_url, o.repo_url, o.status AS opp_status
               FROM patch_queue p
               JOIN opportunities o ON p.opportunity_id = o.id
               ORDER BY p.updated_at DESC LIMIT 100"""
        ).fetchall()
        return [dict(r) for r in rows]


def approve_opportunity(opp_id: int) -> Dict[str, Any]:
    db.init_db()
    db.update_opportunity_status(opp_id, "approved")
    task_id = qm.enqueue_task(
        "repair_execute",
        priority=3,
        opportunity_id=opp_id,
        task_data={"opportunity_id": opp_id},
    )
    return {"opportunity_id": opp_id, "task_id": task_id, "status": "approved"}


def generate_patch_for_opportunity(opp_id: int, dry_run: bool = False) -> Optional[Dict[str, Any]]:
    """Run Ollama patch pipeline for a specific opportunity."""
    db.init_db()
    opp = db.get_opportunity(opp_id)
    if not opp:
        return {"error": "Opportunity not found"}
    db.update_opportunity_status(opp_id, "approved")
    result = run_executor(dry_run=dry_run, opp_id=opp_id)
    if result and result.get("state") == "ready_to_submit":
        _upsert_patch_queue(opp_id, result)
    return result


def _upsert_patch_queue(opp_id: int, result: Dict[str, Any]) -> None:
    import json
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM patch_queue WHERE opportunity_id = ?", (opp_id,)
        ).fetchone()
        payload = json.dumps(result)[:50000]
        branch = result.get("branch_name", "")
        if existing:
            conn.execute(
                """UPDATE patch_queue SET status=?, patch_json=?, branch_name=?, updated_at=datetime('now')
                   WHERE opportunity_id=?""",
                ("ready", payload, branch, opp_id),
            )
        else:
            conn.execute(
                """INSERT INTO patch_queue (opportunity_id, status, patch_json, branch_name)
                   VALUES (?, 'ready', ?, ?)""",
                (opp_id, payload, branch),
            )


def submit_patch(opp_id: int) -> Dict[str, Any]:
    return execute_submit(opp_id)


def process_queue_once() -> Optional[Dict[str, Any]]:
    db.init_db()
    qm.initialize_queue()
    task = qm.dequeue_task("sentinel-earn-worker", ["repair_execute"])
    if not task:
        return None
    try:
        result = run_executor(dry_run=False, opp_id=task.get("opportunity_id"))
        qm.complete_task(task["id"], success=bool(result and result.get("success", True)))
        if result and result.get("state") == "ready_to_submit":
            _upsert_patch_queue(task.get("opportunity_id") or 0, result)
        return result
    except Exception as e:
        qm.complete_task(task["id"], success=False, error_message=str(e))
        raise


def start_background_worker(interval_sec: int = 30) -> None:
    global _worker_thread, _worker_running
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_running = True

    def _loop():
        while _worker_running:
            try:
                settings = load_settings()
                pending = qm.list_tasks(status="pending", limit=5)
                if pending:
                    process_queue_once()
                elif settings.get("auto_generate_patches"):
                    run_full_cycle()
            except Exception as e:
                logger.exception("Worker loop error: %s", e)
            time.sleep(interval_sec)

    _worker_thread = threading.Thread(target=_loop, name="sentinel-earn-worker", daemon=True)
    _worker_thread.start()


def stop_background_worker() -> None:
    global _worker_running
    _worker_running = False
