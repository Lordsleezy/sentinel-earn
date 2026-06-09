"""
git_operations.py — Git-native safety operations for Sentinel Earn
Replaces file-based rollback with isolated branches + hard reset
Implements atomic git operations with full rollback guarantees
"""
import logging
import shutil
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime

import git
from git.exc import GitCommandError

logger = logging.getLogger(__name__)


# ─── Branch Naming ────────────────────────────────────────────────────────────

def safe_branch_name(issue_id: int, timestamp: Optional[int] = None) -> str:
    """
    Generate a safe, unique branch name for an issue fix.
    Format: sentinel-fix-{issue_id}-{timestamp}
    """
    if timestamp is None:
        timestamp = int(datetime.now().timestamp())
    return f"sentinel-fix-{issue_id}-{timestamp}"


# ─── Repository Initialization ────────────────────────────────────────────────

def clone_repo_safe(
    clone_url: str,
    target: Path,
    token: Optional[str] = None,
    depth: int = 1,
    dry_run: bool = False
) -> Optional[git.Repo]:
    """
    Clone repository with safety checks and auth injection.
    
    Args:
        clone_url: HTTPS git URL
        target: Local path to clone into
        token: GitHub token for authentication
        depth: Clone depth (1 for shallow clone)
        dry_run: If True, skip actual clone
    
    Returns:
        git.Repo object or None on failure
    """
    if dry_run:
        logger.info(f"[DRY RUN] Would clone {clone_url} → {target}")
        return None
    
    try:
        # Clean target if exists
        if target.exists():
            logger.info(f"Removing existing directory: {target}")
            shutil.rmtree(target, ignore_errors=True)
        
        # Inject token into URL if provided
        auth_url = clone_url
        if token and "github.com" in clone_url:
            auth_url = clone_url.replace("https://", f"https://{token}@")
        
        logger.info(f"Cloning {clone_url} (depth={depth})...")
        repo = git.Repo.clone_from(
            auth_url,
            str(target),
            depth=depth,
            no_checkout=False,  # We need files checked out
            config='core.autocrlf=false'  # Preserve line endings
        )
        
        logger.info(f"Cloned successfully to {target}")
        return repo
        
    except GitCommandError as e:
        logger.error(f"Git clone failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Clone error: {e}")
        return None


# ─── Isolated Branch Operations ───────────────────────────────────────────────

def create_fix_branch(
    repo: git.Repo,
    branch_name: str,
    base_branch: str = "main"
) -> bool:
    """
    Create an isolated fix branch from base branch.
    
    Args:
        repo: GitPython Repo object
        branch_name: Name for the new branch
        base_branch: Branch to base the fix on (usually 'main' or 'master')
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure we're on the base branch
        if repo.active_branch.name != base_branch:
            logger.info(f"Checking out base branch: {base_branch}")
            repo.git.checkout(base_branch)
        
        # Create and checkout new branch
        logger.info(f"Creating fix branch: {branch_name}")
        repo.git.checkout("-b", branch_name)
        
        logger.info(f"Now on branch: {repo.active_branch.name}")
        return True
        
    except GitCommandError as e:
        logger.error(f"Failed to create branch {branch_name}: {e}")
        return False


# ─── Hard Reset & Cleanup ─────────────────────────────────────────────────────

def hard_reset_repo(repo: git.Repo, ref: str = "HEAD") -> bool:
    """
    Hard reset repository to a specific ref, discarding all changes.
    
    Args:
        repo: GitPython Repo object
        ref: Git reference to reset to (default: HEAD)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"Hard resetting to {ref}...")
        repo.git.reset("--hard", ref)
        logger.info("Hard reset complete")
        return True
    except GitCommandError as e:
        logger.error(f"Hard reset failed: {e}")
        return False


def clean_untracked(repo: git.Repo, force: bool = True) -> bool:
    """
    Remove all untracked files and directories.
    
    Args:
        repo: GitPython Repo object
        force: If True, use -fd flags (force + directories)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        flags = "-fd" if force else "-f"
        logger.info(f"Cleaning untracked files ({flags})...")
        repo.git.clean(flags)
        logger.info("Clean complete")
        return True
    except GitCommandError as e:
        logger.error(f"Git clean failed: {e}")
        return False


def rollback_attempt(repo: git.Repo, base_branch: str = "main") -> bool:
    """
    Full rollback: hard reset + clean + return to base branch.
    
    Args:
        repo: GitPython Repo object
        base_branch: Branch to return to after rollback
    
    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info("Rolling back all changes...")
        
        # Hard reset current branch
        if not hard_reset_repo(repo, "HEAD"):
            return False
        
        # Clean untracked files
        if not clean_untracked(repo):
            return False
        
        # Return to base branch
        try:
            repo.git.checkout(base_branch)
            logger.info(f"Returned to {base_branch}")
        except GitCommandError:
            logger.warning(f"Could not return to {base_branch} (may not exist)")
        
        logger.info("Rollback complete")
        return True
        
    except Exception as e:
        logger.error(f"Rollback failed: {e}")
        return False


# ─── Commit & Push ────────────────────────────────────────────────────────────

def commit_changes(
    repo: git.Repo,
    message: str,
    files: Optional[list] = None
) -> bool:
    """
    Stage and commit changes.
    
    Args:
        repo: GitPython Repo object
        message: Commit message
        files: List of file paths to stage (None = stage all modified)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Stage files
        if files:
            logger.info(f"Staging {len(files)} file(s)...")
            repo.index.add(files)
        else:
            logger.info("Staging all modified files...")
            repo.git.add("-A")
        
        # Check if there are changes to commit
        if not repo.index.diff("HEAD"):
            logger.warning("No changes to commit")
            return False
        
        # Commit
        logger.info(f"Committing: {message[:60]}...")
        repo.index.commit(message)
        logger.info("Commit successful")
        return True
        
    except GitCommandError as e:
        logger.error(f"Commit failed: {e}")
        return False


def push_branch(
    repo: git.Repo,
    branch_name: str,
    remote: str = "origin",
    force: bool = False
) -> bool:
    """
    Push branch to remote.
    
    Args:
        repo: GitPython Repo object
        branch_name: Branch to push
        remote: Remote name (default: origin)
        force: If True, force push
    
    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"Pushing {branch_name} to {remote}...")
        
        if force:
            repo.git.push(remote, branch_name, "--force")
        else:
            repo.git.push(remote, branch_name)
        
        logger.info("Push successful")
        return True
        
    except GitCommandError as e:
        logger.error(f"Push failed: {e}")
        return False


# ─── Atomic Fix Application ───────────────────────────────────────────────────

def apply_fix_atomic(
    repo: git.Repo,
    issue_id: int,
    modified_files: list,
    commit_message: str,
    base_branch: str = "main",
    dry_run: bool = False
) -> Tuple[bool, Optional[str]]:
    """
    Atomically apply a fix: create branch → commit → push.
    On ANY failure: rollback everything.
    
    Args:
        repo: GitPython Repo object
        issue_id: Issue ID for branch naming
        modified_files: List of file paths that were modified
        commit_message: Commit message
        base_branch: Base branch to branch from
        dry_run: If True, skip actual git operations
    
    Returns:
        (success: bool, branch_name: Optional[str])
    """
    if dry_run:
        branch_name = safe_branch_name(issue_id)
        logger.info(f"[DRY RUN] Would apply fix on branch: {branch_name}")
        return True, branch_name
    
    branch_name = safe_branch_name(issue_id)
    original_branch = repo.active_branch.name if repo.head.is_detached is False else None
    
    try:
        # Step 1: Create isolated branch
        if not create_fix_branch(repo, branch_name, base_branch):
            raise RuntimeError("Failed to create fix branch")
        
        # Step 2: Commit changes
        if not commit_changes(repo, commit_message, modified_files):
            raise RuntimeError("Failed to commit changes")
        
        # Step 3: Push to remote
        if not push_branch(repo, branch_name):
            raise RuntimeError("Failed to push branch")
        
        logger.info(f"✓ Fix applied successfully on branch: {branch_name}")
        return True, branch_name
        
    except Exception as e:
        logger.error(f"Atomic fix application failed: {e}")
        logger.info("Initiating rollback...")
        
        # Rollback: reset + clean + return to original branch
        rollback_attempt(repo, original_branch or base_branch)
        
        return False, None


# ─── Workspace Cleanup ────────────────────────────────────────────────────────

def cleanup_workspace(path: Path, preserve_on_error: bool = False) -> bool:
    """
    Clean up workspace directory.
    
    Args:
        path: Path to workspace directory
        preserve_on_error: If True, don't delete on cleanup errors
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if not path.exists():
            return True
        
        logger.info(f"Cleaning up workspace: {path}")
        shutil.rmtree(path, ignore_errors=not preserve_on_error)
        
        if path.exists():
            logger.warning(f"Workspace still exists after cleanup: {path}")
            return False
        
        logger.info("Workspace cleaned")
        return True
        
    except Exception as e:
        logger.error(f"Workspace cleanup failed: {e}")
        return False


# ─── Repository Validation ────────────────────────────────────────────────────

def validate_repo_url(url: str) -> bool:
    """
    Validate that a URL is a safe GitHub repository URL.
    
    Args:
        url: Repository URL to validate
    
    Returns:
        True if valid, False otherwise
    """
    if not url:
        return False
    
    # Must be HTTPS
    if not url.startswith("https://"):
        logger.warning(f"Invalid URL scheme (must be HTTPS): {url}")
        return False
    
    # Must be github.com
    if "github.com" not in url:
        logger.warning(f"Only GitHub URLs supported: {url}")
        return False
    
    # Must end with .git or be a valid GitHub URL format
    if not (url.endswith(".git") or "/github.com/" in url):
        logger.warning(f"Invalid GitHub URL format: {url}")
        return False
    
    # No shell metacharacters
    dangerous_chars = [";", "&", "|", "`", "$", "(", ")", "<", ">", "\n", "\r"]
    if any(char in url for char in dangerous_chars):
        logger.error(f"Dangerous characters in URL: {url}")
        return False
    
    return True


def get_repo_info(repo: git.Repo) -> dict:
    """
    Get repository information for logging/debugging.
    
    Args:
        repo: GitPython Repo object
    
    Returns:
        Dictionary with repo info
    """
    try:
        return {
            "active_branch": repo.active_branch.name if not repo.head.is_detached else "DETACHED",
            "is_dirty": repo.is_dirty(),
            "untracked_files": len(repo.untracked_files),
            "remotes": [r.name for r in repo.remotes],
            "head_commit": str(repo.head.commit)[:8],
        }
    except Exception as e:
        logger.warning(f"Could not get repo info: {e}")
        return {}
