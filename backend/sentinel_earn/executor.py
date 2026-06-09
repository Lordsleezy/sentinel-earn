"""
executor.py — Integrated execution pipeline for Sentinel Earn
Full dry-run execution loop with operational integration:
- context_builder for intelligent file selection
- patch_engine for deterministic patch application
- test_runner for verification
- git_operations for atomic git operations
- security checks before repo cloning
- structured execution states
- comprehensive logging
"""
import os
import re
import json
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from enum import Enum

import httpx
import git
from dotenv import load_dotenv

from sentinel_earn import db
from sentinel_earn.notifications import send_notification
from sentinel_earn.prompt_engine import run_fix_pipeline, _extract_keywords
from sentinel_earn.context_builder import build_context, format_context_for_prompt
from sentinel_earn.patch_engine import apply_patches_atomic, validate_patch_json, preview_patches
from sentinel_earn.test_runner import run_tests, format_test_result, TestFramework
from sentinel_earn.git_operations import (
    clone_repo_safe, create_fix_branch, commit_changes, push_branch,
    rollback_attempt, cleanup_workspace, validate_repo_url, safe_branch_name
)
from sentinel_earn.security import validate_git_url, audit_repository, format_security_check
from sentinel_earn.config import get_workspace_dir, apply_settings

apply_settings()

GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")
WORKSPACE_DIR   = get_workspace_dir()
TARGET_LANGUAGES = {"python", "javascript", "typescript"}

logger = logging.getLogger(__name__)


# ─── Execution States ─────────────────────────────────────────────────────────

class ExecutionState(Enum):
    """Structured execution states for tracking progress."""
    DISCOVERED = "discovered"
    ANALYZING = "analyzing"
    PATCHING = "patching"
    TESTING = "testing"
    VERIFYING = "verifying"
    READY_TO_SUBMIT = "ready_to_submit"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


# ─── Execution Logging ────────────────────────────────────────────────────────

class ExecutionLogger:
    """Structured logging for execution pipeline."""
    
    def __init__(self, opp_id: int):
        self.opp_id = opp_id
        self.logs: List[Dict] = []
        self.start_time = datetime.now()
    
    def log(self, event: str, details: str = "", state: Optional[ExecutionState] = None):
        """Log an execution event."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "details": details,
            "state": state.value if state else None,
            "elapsed_seconds": (datetime.now() - self.start_time).total_seconds()
        }
        self.logs.append(entry)
        
        # Also log to database
        db.log_event(event, details[:500], self.opp_id)
        
        # Log to standard logger
        log_msg = f"[{event}] {details[:200]}"
        if state:
            log_msg = f"[{state.value.upper()}] {log_msg}"
        logger.info(log_msg)
    
    def get_summary(self) -> str:
        """Get execution summary."""
        total_time = (datetime.now() - self.start_time).total_seconds()
        return f"Execution completed in {total_time:.1f}s with {len(self.logs)} events"


# ─── GitHub API helpers ───────────────────────────────────────────────────────

def _gh_headers() -> Dict[str, str]:
    h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SentinelEarn/1.0"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h


def get_issue_details(issue_url: str) -> Optional[Dict]:
    """Fetch issue body, comments, and repo metadata from GitHub API."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", issue_url)
    if not m:
        logger.warning(f"Cannot parse issue URL: {issue_url}")
        return None

    owner, repo, issue_num = m.groups()
    api_base = f"https://api.github.com/repos/{owner}/{repo}"

    try:
        with httpx.Client(timeout=30.0) as client:
            # Issue body
            r_issue = client.get(f"{api_base}/issues/{issue_num}", headers=_gh_headers())
            r_issue.raise_for_status()
            issue = r_issue.json()

            # Comments (latest 10)
            r_comments = client.get(
                f"{api_base}/issues/{issue_num}/comments",
                headers=_gh_headers(),
                params={"per_page": 10},
            )
            comments = r_comments.json() if r_comments.status_code == 200 else []

            # Repo metadata (language, default branch)
            r_repo = client.get(api_base, headers=_gh_headers())
            repo_info = r_repo.json() if r_repo.status_code == 200 else {}

            language = (repo_info.get("language") or "python").lower()
            if language not in TARGET_LANGUAGES:
                language = "python"

            return {
                "title":          issue.get("title", ""),
                "body":           issue.get("body", "") or "",
                "comments":       [c.get("body", "") for c in comments],
                "labels":         [lb["name"] for lb in issue.get("labels", [])],
                "issue_url":      issue_url,
                "owner":          owner,
                "repo":           repo,
                "issue_num":      int(issue_num),
                "language":       language,
                "clone_url":      repo_info.get("clone_url",
                                                f"https://github.com/{owner}/{repo}.git"),
                "default_branch": repo_info.get("default_branch", "main"),
            }
    except Exception as e:
        logger.error(f"Failed to fetch issue {issue_url}: {e}")
        return None


# ─── Fork & Clone ─────────────────────────────────────────────────────────────

def fork_repo(owner: str, repo: str, dry_run: bool = False) -> Optional[str]:
    """Fork repo to GITHUB_USERNAME and return clone URL."""
    if dry_run:
        logger.info(f"[DRY RUN] Would fork {owner}/{repo}")
        return f"https://github.com/{GITHUB_USERNAME or 'DRY_RUN_USER'}/{repo}.git"

    if not GITHUB_TOKEN or not GITHUB_USERNAME:
        logger.error("GITHUB_TOKEN and GITHUB_USERNAME required for forking")
        return None

    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"https://api.github.com/repos/{owner}/{repo}/forks",
                headers=_gh_headers(),
            )
            if r.status_code in (200, 202):
                return r.json().get(
                    "clone_url", f"https://github.com/{GITHUB_USERNAME}/{repo}.git"
                )
            logger.error(f"Fork failed {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"Fork error: {e}")
        return None


# ─── Git operations ───────────────────────────────────────────────────────────

def create_pull_request(
    owner: str, repo: str, branch: str,
    title: str, body: str, default_branch: str,
    dry_run: bool = False,
) -> Optional[str]:
    if dry_run:
        url = f"https://github.com/{owner}/{repo}/pull/DRYRUN"
        logger.info(f"[DRY RUN] Would open PR: {url}")
        return url

    if not GITHUB_TOKEN or not GITHUB_USERNAME:
        logger.error("GitHub credentials required for PR creation")
        return None

    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                headers=_gh_headers(),
                json={
                    "title": title,
                    "body":  body,
                    "head":  f"{GITHUB_USERNAME}:{branch}",
                    "base":  default_branch,
                    "draft": False,
                },
            )
            if r.status_code == 201:
                return r.json()["html_url"]
            logger.error(f"PR creation failed {r.status_code}: {r.text[:300]}")
            return None
    except Exception as e:
        logger.error(f"PR creation error: {e}")
        return None


def post_issue_comment(
    owner: str, repo: str, issue_num: int, comment: str, dry_run: bool = False
) -> bool:
    if dry_run:
        logger.info(f"[DRY RUN] Would comment on {owner}/{repo}#{issue_num}")
        return True
    if not GITHUB_TOKEN:
        return False
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_num}/comments",
                headers=_gh_headers(),
                json={"body": comment},
            )
            return r.status_code == 201
    except Exception as e:
        logger.error(f"Comment error: {e}")
        return False


# ─── PR body builder ─────────────────────────────────────────────────────────

def _build_pr_body(issue_details: Dict, fix_result: Dict, issue_url: str) -> str:
    cot = fix_result.get("chain_of_thought", {})
    return (
        f"## Fix for: {issue_details['title']}\n\n"
        f"Closes {issue_url}\n\n"
        f"### Diagnosis\n{fix_result.get('diagnosis', fix_result.get('explanation', ''))}\n\n"
        f"### Root Cause\n{cot.get('root_cause', 'See diagnosis above')}\n\n"
        f"### Change Summary\n{cot.get('minimal_change', fix_result.get('explanation', ''))}\n\n"
        f"### Verification\n{cot.get('verification', 'Manual testing recommended')}\n\n"
        f"---\n"
        f"*Generated by Sentinel Earn — confidence score {fix_result.get('confidence', 0)}/10*\n"
    )


def _get_latest_branch_for_opportunity(opp_id: int) -> Optional[str]:
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT detail FROM agent_log
               WHERE opportunity_id = ? AND event = 'branch_create'
               ORDER BY id DESC LIMIT 1""",
            (opp_id,)
        ).fetchone()
    if not row:
        return None
    detail = row["detail"]
    prefix = "Creating branch: "
    if detail.startswith(prefix):
        return detail[len(prefix):].strip()
    return detail.strip() or None


def execute_submit(opp_id: int) -> Dict:
    opp = db.get_opportunity(opp_id)
    if not opp:
        return {"success": False, "error": "Opportunity not found", "opp_id": opp_id}

    if opp.get("status") != "ready_to_submit":
        return {
            "success": False,
            "error": f"Opportunity status must be ready_to_submit, got {opp.get('status')}",
            "opp_id": opp_id,
        }

    if not GITHUB_TOKEN or not GITHUB_USERNAME:
        return {"success": False, "error": "GitHub credentials required for submission", "opp_id": opp_id}

    issue = get_issue_details(opp["issue_url"])
    if not issue:
        return {"success": False, "error": "Could not fetch issue details", "opp_id": opp_id}

    branch_name = _get_latest_branch_for_opportunity(opp_id)
    if not branch_name:
        return {"success": False, "error": "No prepared branch found", "opp_id": opp_id}

    workspace = WORKSPACE_DIR / f"opp_{opp_id}"
    if not workspace.exists():
        return {"success": False, "error": f"Prepared workspace missing: {workspace}", "opp_id": opp_id}

    try:
        repo_obj = git.Repo(str(workspace))
    except Exception as e:
        return {"success": False, "error": f"Could not open prepared repository: {e}", "opp_id": opp_id}

    exec_log = ExecutionLogger(opp_id)
    current_state = ExecutionState.READY_TO_SUBMIT

    exec_log.log("submit_start", f"Submitting branch: {branch_name}", current_state)

    if not push_branch(repo_obj, branch_name):
        exec_log.log("push_failed", "Failed to push branch", ExecutionState.FAILED)
        return {"success": False, "error": "Failed to push branch", "opp_id": opp_id}

    exec_log.log("push_success", f"Pushed branch: {branch_name}", current_state)

    pr_body = _build_pr_body(issue, {"confidence": 0}, opp["issue_url"])
    pr_url = create_pull_request(
        issue["owner"],
        issue["repo"],
        branch_name,
        title=f"fix: {issue['title'][:72]}",
        body=pr_body,
        default_branch=issue["default_branch"],
    )
    if not pr_url:
        exec_log.log("pr_failed", "PR creation failed", ExecutionState.FAILED)
        return {"success": False, "error": "PR creation failed", "opp_id": opp_id}

    exec_log.log("pr_created", pr_url, current_state)
    send_notification("Sentinel Earn PR created", pr_url, priority="default", tags="white_check_mark")

    post_issue_comment(
        issue["owner"], issue["repo"], issue["issue_num"],
        f"I've submitted a fix for this issue: {pr_url}\n\nPlease review!",
    )

    db.insert_submission(opp_id, pr_url)
    db.update_opportunity_status(opp_id, "submitted")
    exec_log.log("submission_complete", f"PR={pr_url}", current_state)

    cleanup_workspace(workspace)

    return {
        "success": True,
        "status": "submitted",
        "pr_url": pr_url,
        "opp_id": opp_id,
        "state": current_state.value,
        "execution_log": exec_log.logs,
    }


# ─── Main executor ────────────────────────────────────────────────────────────

def run_executor(dry_run: bool = False, opp_id: Optional[int] = None) -> Optional[Dict]:
    """
    Full execution pipeline for one opportunity:
      get top opp (or specific opp_id) → fetch issue → security check → fork → clone →
      build context → run_fix_pipeline → apply patch → run tests →
      commit/push → PR → comment → log
    
    Returns execution result with detailed state tracking.
    """
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    if opp_id is not None:
        opp = db.get_opportunity(opp_id)
        if opp and opp.get("status") not in ("new", "approved", "ready_to_submit"):
            db.update_opportunity_status(opp_id, "approved")
    else:
        opp = db.get_top_opportunity()
    if not opp:
        logger.info("No new opportunities in queue")
        db.log_event("executor_skip", "No unstarted opportunities available")
        return None

    opp_id = opp["id"]
    exec_log = ExecutionLogger(opp_id)
    
    logger.info(f"Executor: opportunity #{opp_id} — {opp['title'][:70]}")
    exec_log.log("executor_start", f"Starting: {opp['title'][:120]}", ExecutionState.DISCOVERED)
    db.update_opportunity_status(opp_id, "in_progress")

    workspace = WORKSPACE_DIR / f"opp_{opp_id}"
    repo_obj: Optional[git.Repo] = None
    current_state = ExecutionState.DISCOVERED
    preserve_workspace = False

    try:
        # ── 1. Fetch full issue details ───────────────────────────────────────
        current_state = ExecutionState.ANALYZING
        exec_log.log("fetch_issue", "Fetching issue details from GitHub", current_state)
        
        issue = get_issue_details(opp["issue_url"])
        if not issue:
            exec_log.log("fetch_failed", "Could not fetch issue details", ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            return None

        owner    = issue["owner"]
        repo     = issue["repo"]
        issue_num = issue["issue_num"]
        language = issue["language"]
        default_branch = issue["default_branch"]
        clone_url = issue["clone_url"]

        exec_log.log("issue_fetched", f"repo={owner}/{repo} lang={language}", current_state)

        # ── 2. Security validation BEFORE cloning ─────────────────────────────
        exec_log.log("security_check", "Validating repository URL", current_state)
        
        if not validate_git_url(clone_url):
            exec_log.log("security_failed", f"Invalid/unsafe URL: {clone_url}", ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            return None
        
        exec_log.log("security_passed", "URL validation passed", current_state)

        # ── 3. Dry-run short-circuit ──────────────────────────────────────────
        if dry_run:
            exec_log.log("dry_run_mode", f"Issue: {issue['title']}", current_state)
            logger.info(f"[DRY RUN] Repo: {owner}/{repo}, Language: {language}")
            
            # Build context (dry-run safe)
            issue_text = issue["title"] + " " + (issue["body"] or "")
            exec_log.log("dry_run_context", "Building context (dry-run)", current_state)
            
            # Simulate context building without actual repo
            result = run_fix_pipeline(issue, {}, language, dry_run=True)
            exec_log.log("dry_run_complete", json.dumps(result)[:300], ExecutionState.READY_TO_SUBMIT)
            
            db.update_opportunity_status(opp_id, "new")  # Reset so real run can pick it up
            return result

        # ── 4. Fork & clone ───────────────────────────────────────────────────
        exec_log.log("fork_start", f"Forking {owner}/{repo}", current_state)
        
        fork_url = fork_repo(owner, repo)
        if not fork_url:
            exec_log.log("fork_failed", "Fork operation failed", ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            return None

        exec_log.log("fork_success", f"Fork URL: {fork_url}", current_state)
        exec_log.log("clone_start", f"Cloning to {workspace}", current_state)
        
        repo_obj = clone_repo_safe(fork_url, workspace, token=GITHUB_TOKEN, depth=1)
        if not repo_obj:
            exec_log.log("clone_failed", "Clone operation failed", ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            return None

        exec_log.log("clone_success", f"Cloned to {workspace}", current_state)

        # ── 5. Post-clone security audit ──────────────────────────────────────
        exec_log.log("security_audit", "Running repository security audit", current_state)
        
        security_check = audit_repository(workspace)
        exec_log.log("security_audit_result", format_security_check(security_check), current_state)
        
        if not security_check.passed:
            exec_log.log("security_audit_failed", 
                        f"Security audit failed: {security_check.issues}", 
                        ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            cleanup_workspace(workspace)
            return None

        # ── 6. Build intelligent context ──────────────────────────────────────
        exec_log.log("context_build", "Building intelligent context", current_state)
        
        issue_text = issue["title"] + " " + (issue["body"] or "")
        for comment in issue.get("comments", [])[:3]:
            issue_text += " " + comment
        
        context_map = build_context(
            repo_dir=workspace,
            issue_text=issue_text,
            language=language,
            max_files=15,
            max_total_tokens=8000
        )
        
        exec_log.log("context_built", 
                    f"{len(context_map)} files selected, "
                    f"avg relevance: {sum(c.relevance_score for c in context_map.values()) / max(len(context_map), 1):.1f}",
                    current_state)
        
        # Convert to old format for prompt_engine compatibility
        files_dict = {path: ctx.compressed_content or ctx.content 
                     for path, ctx in context_map.items()}

        # ── 7. Run fix pipeline ───────────────────────────────────────────────
        current_state = ExecutionState.PATCHING
        exec_log.log("fix_pipeline_start", f"Running fix pipeline with {len(files_dict)} files", current_state)
        
        result = run_fix_pipeline(issue, files_dict, language)

        if not result:
            exec_log.log("fix_pipeline_failed", "Fix pipeline returned no result", ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            cleanup_workspace(workspace)
            return None

        if result.get("skipped"):
            exec_log.log("complexity_skip", result.get("reason", "Too complex"), ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "skipped")
            cleanup_workspace(workspace)
            return result

        confidence = result.get("confidence", 0)
        if confidence < 7:
            exec_log.log("low_confidence", f"confidence={confidence}/10 (threshold: 7)", ExecutionState.FAILED)
            logger.info(f"Confidence {confidence}/10 — below threshold, skipping")
            db.update_opportunity_status(opp_id, "skipped")
            cleanup_workspace(workspace)
            return result

        exec_log.log("fix_generated", 
                    f"confidence={confidence}/10, files={len(result.get('fix',{}).get('files',[]))}",
                    current_state)

        # ── 8. Preview and validate patch ─────────────────────────────────────
        exec_log.log("patch_preview", preview_patches(result), current_state)
        
        is_valid, errors = validate_patch_json(result)
        if not is_valid:
            exec_log.log("patch_invalid", f"Patch validation failed: {errors}", ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            cleanup_workspace(workspace)
            return None

        # ── 9. Run baseline tests (before patch) ──────────────────────────────
        current_state = ExecutionState.TESTING
        exec_log.log("baseline_tests", "Running baseline tests", current_state)
        
        baseline_result = run_tests(workspace, timeout=300)
        exec_log.log("baseline_complete", format_test_result(baseline_result), current_state)

        # ── 10. Apply patches atomically ──────────────────────────────────────
        exec_log.log("patch_apply", "Applying patches atomically", current_state)
        
        patch_result = apply_patches_atomic(workspace, result, use_fuzzy=True)
        
        if not patch_result.success:
            exec_log.log("patch_failed", 
                        f"Patch application failed: {patch_result.errors}",
                        ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            cleanup_workspace(workspace)
            return None

        modified_files = patch_result.files_modified + patch_result.files_created
        exec_log.log("patch_applied", 
                    f"Modified: {patch_result.files_modified}, Created: {patch_result.files_created}",
                    current_state)

        # ── 11. Run post-patch tests ──────────────────────────────────────────
        current_state = ExecutionState.VERIFYING
        exec_log.log("post_patch_tests", "Running post-patch tests", current_state)
        
        post_patch_result = run_tests(workspace, timeout=300)
        exec_log.log("post_patch_complete", format_test_result(post_patch_result), current_state)

        # ── 12. Verify test results ───────────────────────────────────────────
        if baseline_result.success and not post_patch_result.success:
            exec_log.log("tests_regressed", 
                        f"Tests regressed: {baseline_result.passed} → {post_patch_result.passed}",
                        ExecutionState.FAILED)
            logger.error("Tests regressed after patch — rolling back")
            rollback_attempt(repo_obj, default_branch)
            db.update_opportunity_status(opp_id, "failed")
            cleanup_workspace(workspace)
            return None
        
        if post_patch_result.success or post_patch_result.passed > baseline_result.passed:
            exec_log.log("tests_improved", 
                        f"Tests improved: {baseline_result.passed} → {post_patch_result.passed}",
                        current_state)

        # ── 13. Create fix branch ─────────────────────────────────────────────
        current_state = ExecutionState.READY_TO_SUBMIT
        branch_name = safe_branch_name(opp_id)
        exec_log.log("branch_create", f"Creating branch: {branch_name}", current_state)
        
        if not create_fix_branch(repo_obj, branch_name, default_branch):
            exec_log.log("branch_failed", "Failed to create fix branch", ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            cleanup_workspace(workspace)
            return None

        # ── 14. Commit changes ────────────────────────────────────────────────
        commit_msg = (
            f"fix: {issue['title'][:72]}\n\n"
            f"Fixes #{issue_num}\n\n"
            f"Generated by Sentinel Earn (confidence: {confidence}/10)"
        )
        exec_log.log("commit_start", f"Committing {len(modified_files)} files", current_state)
        
        if not commit_changes(repo_obj, commit_msg, modified_files):
            exec_log.log("commit_failed", "Failed to commit changes", ExecutionState.FAILED)
            db.update_opportunity_status(opp_id, "failed")
            cleanup_workspace(workspace)
            return None

        exec_log.log("commit_success", f"Committed: {commit_msg[:100]}", current_state)

        db.update_opportunity_status(opp_id, "ready_to_submit")
        exec_log.log(
            "ready_to_submit",
            f"Prepared branch {branch_name}; awaiting approval before push/PR",
            current_state
        )
        send_notification(
            "SentinelAI approval needed",
            f"Repair #{opp_id} is READY_TO_SUBMIT: {opp['title'][:120]}",
            priority="high",
            tags="warning",
        )
        preserve_workspace = True
        logger.info(f"✓ Opportunity #{opp_id} ready to submit — awaiting approval")
        logger.info(exec_log.get_summary())
        
        return {
            "success": True,
            "status": "ready_to_submit",
            "pr_url": None,
            "confidence": confidence,
            "opp_id": opp_id,
            "state": current_state.value,
            "execution_log": exec_log.logs,
            "baseline_tests": {
                "passed": baseline_result.passed,
                "failed": baseline_result.failed,
                "total": baseline_result.total_tests
            },
            "post_patch_tests": {
                "passed": post_patch_result.passed,
                "failed": post_patch_result.failed,
                "total": post_patch_result.total_tests
            }
        }

    except Exception as exc:
        logger.exception(f"Executor uncaught exception for #{opp_id}: {exc}")
        exec_log.log("executor_exception", str(exc)[:500], ExecutionState.FAILED)
        send_notification(
            "SentinelAI repair error",
            f"Repair #{opp_id} failed: {exc}",
            priority="high",
            tags="rotating_light",
        )
        
        # Attempt rollback if we have a repo object
        if repo_obj:
            exec_log.log("rollback_start", "Attempting rollback", ExecutionState.ROLLED_BACK)
            rollback_attempt(repo_obj, issue.get("default_branch", "main") if issue else "main")
            exec_log.log("rollback_complete", "Rollback completed", ExecutionState.ROLLED_BACK)
        
        db.update_opportunity_status(opp_id, "failed")
        return None

    finally:
        if not preserve_workspace:
            cleanup_workspace(workspace)
        exec_log.log("cleanup_complete", "Workspace cleaned up", current_state)
