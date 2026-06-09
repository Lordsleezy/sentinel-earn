from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import builtins

import requests

from sentinel_earn import db
from sentinel_earn import queue_manager as qm


USER_AGENT = "SentinelEarn/1.0 (+https://github.com/Lordsleezy/sentinel-earn)"
GITHUB_API = "https://api.github.com"


def _headers() -> Dict[str, str]:
    h = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get_json(url: str, params: Optional[Dict] = None) -> Dict:
    response = requests.get(url, headers=_headers(), params=params or {}, timeout=15)
    response.raise_for_status()
    return response.json()


def find_bounty_issues(max=20) -> List[Dict]:
    seen = set()
    found = []
    per_query = builtins.max(10, int(max))
    for label in ("bounty", "good-first-issue"):
        payload = _get_json(
            f"{GITHUB_API}/search/issues",
            {"q": f"label:{label} state:open type:issue", "per_page": per_query},
        )
        for item in payload.get("items", []):
            if item.get("pull_request"):
                continue
            url = item.get("html_url", "")
            if not url or url in seen:
                continue
            repo_api = item.get("repository_url", "")
            repo = repo_api.replace(f"{GITHUB_API}/repos/", "")
            issue = {
                "repo": repo,
                "repo_url": f"https://github.com/{repo}" if repo else "",
                "issue_number": item.get("number"),
                "title": item.get("title", ""),
                "url": url,
                "body": item.get("body") or "",
                "labels": [entry.get("name", "") for entry in item.get("labels", [])],
                "comments": item.get("comments", 0),
                "has_readme": _repo_has_readme(repo),
                "has_tests": _repo_has_tests(repo),
                "language": _repo_language(repo),
                "open_prs_on_issue": _open_prs_on_issue(repo, item.get("number")),
            }
            if issue["has_readme"] and not issue["open_prs_on_issue"]:
                issue["score"] = score_issue(issue)
                found.append(issue)
                seen.add(url)
            if len(found) >= int(max):
                return sorted(found, key=lambda candidate: candidate["score"], reverse=True)
    return sorted(found, key=lambda candidate: candidate["score"], reverse=True)[: int(max)]


def score_issue(issue) -> float:
    score = 0.0
    text = f"{issue.get('title', '')} {issue.get('body', '')} {' '.join(issue.get('labels', []))} {issue.get('language', '')}".lower()
    if issue.get("language", "").lower() in {"python", "javascript"} or "python" in text or "javascript" in text:
        score += 0.3
    if issue.get("has_tests") or "pytest" in text or "jest" in text or "tests/" in text:
        score += 0.2
    if len(issue.get("body", "") or "") > 100:
        score += 0.2
    if not issue.get("open_prs_on_issue") and not issue.get("competing_prs") and not issue.get("pull_request"):
        score += 0.2
    if issue.get("has_readme") or "readme" in text:
        score += 0.1
    return max(0.0, min(1.0, round(score, 4)))


def queue_repair(issue) -> Dict:
    db.init_db()
    qm.initialize_queue()
    opp_id = db.insert_opportunity(
        source="github_bounty",
        title=issue.get("title", ""),
        repo_url=issue.get("repo_url", ""),
        issue_url=issue.get("url", ""),
        bounty_amount=0,
        currency="USD",
        complexity_score=max(1, round((1 - score_issue(issue)) * 10, 2)),
    )
    if opp_id is None:
        existing = next((opp for opp in db.list_opportunities(limit=500) if opp["issue_url"] == issue.get("url", "")), None)
        opp_id = existing["id"] if existing else None
    task_id = None
    if opp_id:
        task_id = qm.enqueue_task("repair_execute", priority=3, opportunity_id=opp_id, task_data={"opportunity_id": opp_id, "issue": issue})
    return {"opportunity_id": opp_id, "task_id": task_id}


def get_pipeline_status() -> Dict:
    qm.initialize_queue()
    stats = qm.get_queue_stats()
    earnings = db.get_earnings_summary()
    last_cycle = _last_cycle_timestamp()
    return {
        "queued": stats.get("pending_count", 0),
        "running": stats.get("running_count", 0),
        "completed": stats.get("completed_count", 0),
        "earned": earnings.get("confirmed_earnings", 0.0),
        "last_cycle": last_cycle,
        "next_cycle": _next_cycle_timestamp(last_cycle),
    }


def run_pipeline_cycle() -> Dict:
    issues = find_bounty_issues(max=20)
    for issue in issues:
        issue["score"] = score_issue(issue)
    ranked = sorted(issues, reverse=True, key=lambda issue: issue["score"])
    queued = []
    for issue in ranked[:3]:
        queued.append({"score": issue["score"], "issue": issue, **queue_repair(issue)})
        db.log_event("bounty_issue_queued", f"{issue.get('url')} score={issue['score']}")
    db.log_event("bounty_pipeline_cycle", f"found={len(issues)} queued={len(queued)}")
    return {"queued": len(queued), "top_issues": ranked[:3], "found": len(issues), "status": get_pipeline_status()}


def _repo_language(repo: str) -> str:
    if not repo:
        return ""
    try:
        return (_get_json(f"{GITHUB_API}/repos/{repo}").get("language") or "")
    except Exception:
        return ""


def _repo_has_readme(repo: str) -> bool:
    if not repo:
        return False
    try:
        _get_json(f"{GITHUB_API}/repos/{repo}/readme")
        return True
    except Exception:
        return False


def _repo_has_tests(repo: str) -> bool:
    if not repo:
        return False
    for candidate in ("tests", "test", "__tests__"):
        try:
            _get_json(f"{GITHUB_API}/repos/{repo}/contents/{candidate}")
            return True
        except Exception:
            continue
    return False


def _open_prs_on_issue(repo: str, issue_number) -> int:
    if not repo or not issue_number:
        return 0
    try:
        payload = _get_json(
            f"{GITHUB_API}/search/issues",
            {"q": f"repo:{repo} type:pr state:open {issue_number}", "per_page": 10},
        )
        return int(payload.get("total_count", len(payload.get("items", []))))
    except Exception:
        return 0


def _last_cycle_timestamp() -> Optional[str]:
    try:
        for event in db.get_recent_logs(limit=100):
            if event.get("event") == "bounty_pipeline_cycle":
                return event.get("timestamp")
    except Exception:
        return None
    return None


def _next_cycle_timestamp(last_cycle: Optional[str]) -> Optional[str]:
    if not last_cycle:
        return None
    try:
        return (datetime.fromisoformat(last_cycle) + timedelta(hours=1)).isoformat()
    except Exception:
        return None
