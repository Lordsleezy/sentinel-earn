"""
security.py — Security and sandboxing for Sentinel Earn
Subprocess isolation, resource limits, path validation
Prevents malicious repos from compromising the agent
"""
import subprocess
import logging
import re
import os
import platform
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ─── Configuration ────────────────────────────────────────────────────────────

# Resource limits
MAX_REPO_SIZE_MB = 500  # Maximum repository size
MAX_FILE_COUNT = 10000  # Maximum number of files
MAX_EXECUTION_SECONDS = 600  # Maximum execution time for any subprocess
MAX_MEMORY_MB = 2048  # Maximum memory per subprocess (if supported)

# Dangerous patterns
DANGEROUS_PATTERNS = [
    r'rm\s+-rf\s+/',
    r':\(\)\{.*\|.*&\s*\}',  # Fork bomb
    r'eval\s*\(',
    r'exec\s*\(',
    r'__import__\s*\(',
    r'subprocess\.call',
    r'os\.system',
    r'curl.*\|.*sh',
    r'wget.*\|.*sh',
]


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class SecurityCheck:
    """Result of a security check."""
    passed: bool
    risk_level: str  # "low", "medium", "high", "critical"
    issues: List[str]
    warnings: List[str]


# ─── Path Validation ──────────────────────────────────────────────────────────

def validate_repo_path(path: Path, workspace_root: Path) -> bool:
    """
    Validate that a path is within the workspace and safe.
    
    Args:
        path: Path to validate
        workspace_root: Root workspace directory
    
    Returns:
        True if safe, False otherwise
    """
    try:
        # Resolve to absolute path
        abs_path = path.resolve()
        abs_workspace = workspace_root.resolve()
        
        # Check if path is within workspace
        try:
            abs_path.relative_to(abs_workspace)
        except ValueError:
            logger.error(f"Path traversal detected: {path} is outside {workspace_root}")
            return False
        
        # Check for suspicious path components
        dangerous_parts = {'.git', '..', '.ssh', '.aws', '.env'}
        if any(part in abs_path.parts for part in dangerous_parts):
            logger.warning(f"Suspicious path component in: {path}")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Path validation error: {e}")
        return False


def sanitize_patch_paths(patch_data: Dict, repo_root: Path) -> bool:
    """
    Validate all file paths in a patch are safe.
    
    Args:
        patch_data: Patch JSON data
        repo_root: Repository root directory
    
    Returns:
        True if all paths are safe, False otherwise
    """
    files = patch_data.get("fix", {}).get("files", [])
    
    for file_patch in files:
        file_path = file_patch.get("path", "")
        
        # Check for path traversal
        if ".." in file_path or file_path.startswith("/"):
            logger.error(f"Path traversal attempt in patch: {file_path}")
            return False
        
        # Validate full path
        full_path = repo_root / file_path
        if not validate_repo_path(full_path, repo_root):
            return False
    
    return True


# ─── URL Validation ───────────────────────────────────────────────────────────

def validate_git_url(url: str) -> bool:
    """
    Validate that a Git URL is safe.
    
    Args:
        url: Git repository URL
    
    Returns:
        True if safe, False otherwise
    """
    if not url:
        return False
    
    # Must be HTTPS
    if not url.startswith("https://"):
        logger.error(f"Only HTTPS URLs allowed: {url}")
        return False
    
    # Must be GitHub (for now)
    if "github.com" not in url:
        logger.error(f"Only GitHub URLs supported: {url}")
        return False
    
    # No shell metacharacters
    dangerous_chars = [";", "&", "|", "`", "$", "(", ")", "<", ">", "\n", "\r", " "]
    if any(char in url for char in dangerous_chars):
        logger.error(f"Dangerous characters in URL: {url}")
        return False
    
    # Valid GitHub URL format
    github_pattern = r'^https://github\.com/[\w\-\.]+/[\w\-\.]+(?:\.git)?$'
    if not re.match(github_pattern, url):
        logger.error(f"Invalid GitHub URL format: {url}")
        return False
    
    return True


# ─── Repository Safety Checks ─────────────────────────────────────────────────

def check_repo_size(repo_dir: Path) -> SecurityCheck:
    """
    Check if repository size is within limits.
    
    Args:
        repo_dir: Repository directory
    
    Returns:
        SecurityCheck result
    """
    issues = []
    warnings = []
    
    try:
        # Count files
        file_count = sum(1 for _ in repo_dir.rglob("*") if _.is_file())
        
        if file_count > MAX_FILE_COUNT:
            issues.append(f"Too many files: {file_count} > {MAX_FILE_COUNT}")
            return SecurityCheck(
                passed=False,
                risk_level="high",
                issues=issues,
                warnings=warnings
            )
        
        # Calculate total size
        total_size = sum(f.stat().st_size for f in repo_dir.rglob("*") if f.is_file())
        total_mb = total_size / (1024 * 1024)
        
        if total_mb > MAX_REPO_SIZE_MB:
            issues.append(f"Repository too large: {total_mb:.1f}MB > {MAX_REPO_SIZE_MB}MB")
            return SecurityCheck(
                passed=False,
                risk_level="high",
                issues=issues,
                warnings=warnings
            )
        
        if total_mb > MAX_REPO_SIZE_MB * 0.7:
            warnings.append(f"Repository is large: {total_mb:.1f}MB")
        
        return SecurityCheck(
            passed=True,
            risk_level="low",
            issues=issues,
            warnings=warnings
        )
        
    except Exception as e:
        logger.error(f"Size check failed: {e}")
        return SecurityCheck(
            passed=False,
            risk_level="medium",
            issues=[f"Size check error: {e}"],
            warnings=[]
        )


def scan_for_malicious_code(repo_dir: Path, max_files: int = 100) -> SecurityCheck:
    """
    Scan repository for obviously malicious code patterns.
    
    Args:
        repo_dir: Repository directory
        max_files: Maximum files to scan
    
    Returns:
        SecurityCheck result
    """
    issues = []
    warnings = []
    
    # Scan source files
    extensions = ['.py', '.js', '.ts', '.sh', '.bash']
    files_scanned = 0
    
    for ext in extensions:
        for filepath in repo_dir.rglob(f'*{ext}'):
            if files_scanned >= max_files:
                break
            
            try:
                content = filepath.read_text(encoding='utf-8', errors='ignore')
                
                # Check for dangerous patterns
                for pattern in DANGEROUS_PATTERNS:
                    if re.search(pattern, content, re.IGNORECASE):
                        issues.append(
                            f"Dangerous pattern in {filepath.name}: {pattern}"
                        )
                
                files_scanned += 1
                
            except Exception:
                continue
    
    if issues:
        return SecurityCheck(
            passed=False,
            risk_level="critical",
            issues=issues,
            warnings=warnings
        )
    
    return SecurityCheck(
        passed=True,
        risk_level="low",
        issues=issues,
        warnings=warnings
    )


def check_repo_metadata(repo_dir: Path) -> SecurityCheck:
    """
    Check repository metadata for red flags.
    
    Args:
        repo_dir: Repository directory
    
    Returns:
        SecurityCheck result
    """
    issues = []
    warnings = []
    
    # Check for .git directory
    if not (repo_dir / ".git").exists():
        warnings.append("No .git directory found")
    
    # Check for suspicious files
    suspicious_files = [
        '.env', '.aws', '.ssh', 'id_rsa', 'id_dsa',
        'credentials', 'secrets', 'private.key'
    ]
    
    for suspicious in suspicious_files:
        if (repo_dir / suspicious).exists():
            warnings.append(f"Suspicious file found: {suspicious}")
    
    # Check for binary files (potential malware)
    binary_extensions = ['.exe', '.dll', '.so', '.dylib', '.bin']
    for ext in binary_extensions:
        binaries = list(repo_dir.rglob(f'*{ext}'))
        if binaries:
            warnings.append(f"Binary files found: {len(binaries)} {ext} files")
    
    risk_level = "medium" if warnings else "low"
    
    return SecurityCheck(
        passed=True,  # Warnings don't fail the check
        risk_level=risk_level,
        issues=issues,
        warnings=warnings
    )


# ─── Safe Subprocess Execution ────────────────────────────────────────────────

def safe_subprocess_run(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: int = MAX_EXECUTION_SECONDS,
    capture_output: bool = True,
    env: Optional[Dict[str, str]] = None,
    check: bool = False
) -> subprocess.CompletedProcess:
    """
    Run subprocess with safety limits.
    
    Args:
        cmd: Command and arguments
        cwd: Working directory
        timeout: Timeout in seconds
        capture_output: Capture stdout/stderr
        env: Environment variables
        check: Raise on non-zero exit
    
    Returns:
        CompletedProcess result
    
    Raises:
        subprocess.TimeoutExpired: If timeout exceeded
        subprocess.CalledProcessError: If check=True and command fails
    """
    # Validate command
    if not cmd or not isinstance(cmd, list):
        raise ValueError("Command must be a non-empty list")
    
    # Never use shell=True
    if any(char in ' '.join(cmd) for char in [';', '&', '|', '`', '$']):
        logger.warning(f"Suspicious characters in command: {cmd}")
    
    # Prepare environment
    safe_env = os.environ.copy()
    if env:
        safe_env.update(env)
    
    # Remove sensitive environment variables
    sensitive_vars = ['GITHUB_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'SSH_PRIVATE_KEY']
    for var in sensitive_vars:
        safe_env.pop(var, None)
    
    # Platform-specific resource limits
    kwargs: Dict[str, Any] = {
        'cwd': str(cwd) if cwd else None,
        'timeout': timeout,
        'capture_output': capture_output,
        'text': True,
        'env': safe_env,
        'check': check,
    }
    
    # On Unix, use resource limits
    if platform.system() != 'Windows':
        try:
            import resource
            
            def set_limits():
                # CPU time limit
                resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))
                # Memory limit (if supported)
                try:
                    max_mem = MAX_MEMORY_MB * 1024 * 1024
                    resource.setrlimit(resource.RLIMIT_AS, (max_mem, max_mem))
                except (ValueError, OSError):
                    pass  # Not all systems support memory limits
            
            kwargs['preexec_fn'] = set_limits
        except ImportError:
            pass
    
    logger.debug(f"Running command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, **kwargs)
        return result
    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timed out after {timeout}s: {cmd}")
        raise
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}: {cmd}")
        raise
    except Exception as e:
        logger.error(f"Command execution error: {e}")
        raise


# ─── Comprehensive Security Audit ─────────────────────────────────────────────

def audit_repository(repo_dir: Path) -> SecurityCheck:
    """
    Run comprehensive security audit on repository.
    
    Args:
        repo_dir: Repository directory
    
    Returns:
        Combined SecurityCheck result
    """
    all_issues = []
    all_warnings = []
    max_risk = "low"
    
    # Run all checks
    checks = [
        ("Size check", check_repo_size(repo_dir)),
        ("Malicious code scan", scan_for_malicious_code(repo_dir)),
        ("Metadata check", check_repo_metadata(repo_dir)),
    ]
    
    for check_name, result in checks:
        logger.info(f"{check_name}: {result.risk_level} risk, {len(result.issues)} issues")
        
        all_issues.extend(result.issues)
        all_warnings.extend(result.warnings)
        
        # Track highest risk level
        risk_levels = ["low", "medium", "high", "critical"]
        if risk_levels.index(result.risk_level) > risk_levels.index(max_risk):
            max_risk = result.risk_level
        
        # Fail fast on critical issues
        if not result.passed and result.risk_level == "critical":
            return SecurityCheck(
                passed=False,
                risk_level="critical",
                issues=all_issues,
                warnings=all_warnings
            )
    
    # Overall pass/fail
    passed = len(all_issues) == 0
    
    return SecurityCheck(
        passed=passed,
        risk_level=max_risk,
        issues=all_issues,
        warnings=all_warnings
    )


def format_security_check(check: SecurityCheck) -> str:
    """
    Format security check result for logging.
    
    Args:
        check: SecurityCheck result
    
    Returns:
        Formatted string
    """
    lines = []
    
    if check.passed:
        lines.append(f"✓ Security check passed (risk: {check.risk_level})")
    else:
        lines.append(f"✗ Security check FAILED (risk: {check.risk_level})")
    
    if check.issues:
        lines.append(f"\nIssues ({len(check.issues)}):")
        for issue in check.issues:
            lines.append(f"  - {issue}")
    
    if check.warnings:
        lines.append(f"\nWarnings ({len(check.warnings)}):")
        for warning in check.warnings[:5]:  # Limit to first 5
            lines.append(f"  - {warning}")
        if len(check.warnings) > 5:
            lines.append(f"  ... and {len(check.warnings) - 5} more")
    
    return '\n'.join(lines)
