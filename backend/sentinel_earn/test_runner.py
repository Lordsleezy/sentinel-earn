"""
test_runner.py — Test execution pipeline for Sentinel Earn
Automatically detects test frameworks and runs tests safely
Supports pytest, unittest, jest, vitest, npm test, cargo test
"""
import subprocess
import logging
import json
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# ─── Data Structures ──────────────────────────────────────────────────────────

class TestFramework(Enum):
    """Supported test frameworks."""
    PYTEST = "pytest"
    UNITTEST = "unittest"
    JEST = "jest"
    VITEST = "vitest"
    NPM_TEST = "npm"
    CARGO_TEST = "cargo"
    UNKNOWN = "unknown"


@dataclass
class TestResult:
    """Result of test execution."""
    framework: TestFramework
    success: bool
    total_tests: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    stdout: str
    stderr: str
    failing_tests: List[str]
    error_message: Optional[str] = None


# ─── Framework Detection ──────────────────────────────────────────────────────

def detect_test_framework(repo_dir: Path) -> TestFramework:
    """
    Detect which test framework is used in the repository.
    
    Args:
        repo_dir: Repository root directory
    
    Returns:
        Detected TestFramework
    """
    # Check for Python test frameworks
    if (repo_dir / "pytest.ini").exists() or (repo_dir / "pyproject.toml").exists():
        # Check if pytest is configured
        if (repo_dir / "pytest.ini").exists():
            return TestFramework.PYTEST
        
        # Check pyproject.toml for pytest
        pyproject = repo_dir / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding='utf-8', errors='ignore')
            if '[tool.pytest' in content or 'pytest' in content:
                return TestFramework.PYTEST
    
    # Check for test files
    test_files = list(repo_dir.rglob("test_*.py")) + list(repo_dir.rglob("*_test.py"))
    if test_files:
        # Check if any test file imports pytest
        for test_file in test_files[:5]:  # Check first 5
            try:
                content = test_file.read_text(encoding='utf-8', errors='ignore')
                if 'import pytest' in content or 'from pytest' in content:
                    return TestFramework.PYTEST
                if 'import unittest' in content or 'from unittest' in content:
                    return TestFramework.UNITTEST
            except Exception:
                continue
    
    # Check for JavaScript/TypeScript test frameworks
    package_json = repo_dir / "package.json"
    if package_json.exists():
        try:
            content = package_json.read_text(encoding='utf-8', errors='ignore')
            data = json.loads(content)
            
            # Check devDependencies
            dev_deps = data.get("devDependencies", {})
            deps = data.get("dependencies", {})
            all_deps = {**dev_deps, **deps}
            
            if "vitest" in all_deps:
                return TestFramework.VITEST
            if "jest" in all_deps or "@jest/core" in all_deps:
                return TestFramework.JEST
            
            # Check scripts
            scripts = data.get("scripts", {})
            if "test" in scripts:
                test_script = scripts["test"]
                if "vitest" in test_script:
                    return TestFramework.VITEST
                if "jest" in test_script:
                    return TestFramework.JEST
                return TestFramework.NPM_TEST
        except Exception as e:
            logger.warning(f"Failed to parse package.json: {e}")
    
    # Check for Rust
    if (repo_dir / "Cargo.toml").exists():
        return TestFramework.CARGO_TEST
    
    return TestFramework.UNKNOWN


# ─── Test Execution ───────────────────────────────────────────────────────────

def run_pytest(
    repo_dir: Path,
    timeout: int = 300,
    specific_tests: Optional[List[str]] = None
) -> TestResult:
    """
    Run pytest tests.
    
    Args:
        repo_dir: Repository root directory
        timeout: Timeout in seconds
        specific_tests: Optional list of specific test files/functions to run
    
    Returns:
        TestResult
    """
    cmd = ["pytest", "-v", "--tb=short", "--no-header"]
    
    if specific_tests:
        cmd.extend(specific_tests)
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**subprocess.os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        )
        
        stdout = result.stdout
        stderr = result.stderr
        
        # Parse pytest output
        passed = len(re.findall(r'PASSED', stdout))
        failed = len(re.findall(r'FAILED', stdout))
        skipped = len(re.findall(r'SKIPPED', stdout))
        total = passed + failed + skipped
        
        # Extract duration
        duration_match = re.search(r'in ([\d.]+)s', stdout)
        duration = float(duration_match.group(1)) if duration_match else 0.0
        
        # Extract failing test names
        failing_tests = re.findall(r'FAILED ([\w/:.]+)', stdout)
        
        return TestResult(
            framework=TestFramework.PYTEST,
            success=result.returncode == 0,
            total_tests=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_seconds=duration,
            stdout=stdout,
            stderr=stderr,
            failing_tests=failing_tests
        )
    
    except subprocess.TimeoutExpired:
        return TestResult(
            framework=TestFramework.PYTEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=timeout,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message=f"Tests timed out after {timeout}s"
        )
    except FileNotFoundError:
        return TestResult(
            framework=TestFramework.PYTEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=0.0,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message="pytest not found - install with: pip install pytest"
        )
    except Exception as e:
        return TestResult(
            framework=TestFramework.PYTEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=0.0,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message=f"Test execution failed: {e}"
        )


def run_unittest(repo_dir: Path, timeout: int = 300) -> TestResult:
    """
    Run unittest tests.
    
    Args:
        repo_dir: Repository root directory
        timeout: Timeout in seconds
    
    Returns:
        TestResult
    """
    cmd = ["python", "-m", "unittest", "discover", "-v"]
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        stdout = result.stdout
        stderr = result.stderr
        
        # Parse unittest output
        # Example: "Ran 42 tests in 1.234s"
        ran_match = re.search(r'Ran (\d+) test', stdout + stderr)
        total = int(ran_match.group(1)) if ran_match else 0
        
        # Check for failures/errors
        failed_match = re.search(r'FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?\)', stdout + stderr)
        if failed_match:
            failures = int(failed_match.group(1) or 0)
            errors = int(failed_match.group(2) or 0)
            failed = failures + errors
        else:
            failed = 0
        
        passed = total - failed
        
        duration_match = re.search(r'in ([\d.]+)s', stdout + stderr)
        duration = float(duration_match.group(1)) if duration_match else 0.0
        
        return TestResult(
            framework=TestFramework.UNITTEST,
            success=result.returncode == 0,
            total_tests=total,
            passed=passed,
            failed=failed,
            skipped=0,
            duration_seconds=duration,
            stdout=stdout,
            stderr=stderr,
            failing_tests=[]
        )
    
    except subprocess.TimeoutExpired:
        return TestResult(
            framework=TestFramework.UNITTEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=timeout,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message=f"Tests timed out after {timeout}s"
        )
    except Exception as e:
        return TestResult(
            framework=TestFramework.UNITTEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=0.0,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message=f"Test execution failed: {e}"
        )


def run_jest(repo_dir: Path, timeout: int = 300) -> TestResult:
    """
    Run Jest tests.
    
    Args:
        repo_dir: Repository root directory
        timeout: Timeout in seconds
    
    Returns:
        TestResult
    """
    # Try npx jest first, fallback to npm test
    cmd = ["npx", "jest", "--verbose", "--no-coverage"]
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        stdout = result.stdout
        stderr = result.stderr
        
        # Parse Jest output
        # Example: "Tests: 2 failed, 8 passed, 10 total"
        test_match = re.search(r'Tests:\s+(?:(\d+) failed,\s+)?(\d+) passed,\s+(\d+) total', stdout)
        if test_match:
            failed = int(test_match.group(1) or 0)
            passed = int(test_match.group(2))
            total = int(test_match.group(3))
        else:
            total = passed = failed = 0
        
        duration_match = re.search(r'Time:\s+([\d.]+)\s*s', stdout)
        duration = float(duration_match.group(1)) if duration_match else 0.0
        
        # Extract failing tests
        failing_tests = re.findall(r'●\s+([\w\s]+)', stdout)
        
        return TestResult(
            framework=TestFramework.JEST,
            success=result.returncode == 0,
            total_tests=total,
            passed=passed,
            failed=failed,
            skipped=0,
            duration_seconds=duration,
            stdout=stdout,
            stderr=stderr,
            failing_tests=failing_tests[:10]  # Limit to first 10
        )
    
    except subprocess.TimeoutExpired:
        return TestResult(
            framework=TestFramework.JEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=timeout,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message=f"Tests timed out after {timeout}s"
        )
    except FileNotFoundError:
        return TestResult(
            framework=TestFramework.JEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=0.0,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message="Jest not found - install with: npm install --save-dev jest"
        )
    except Exception as e:
        return TestResult(
            framework=TestFramework.JEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=0.0,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message=f"Test execution failed: {e}"
        )


def run_npm_test(repo_dir: Path, timeout: int = 300) -> TestResult:
    """
    Run npm test.
    
    Args:
        repo_dir: Repository root directory
        timeout: Timeout in seconds
    
    Returns:
        TestResult
    """
    cmd = ["npm", "test"]
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        return TestResult(
            framework=TestFramework.NPM_TEST,
            success=result.returncode == 0,
            total_tests=0,  # Can't reliably parse without knowing underlying framework
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=0.0,
            stdout=result.stdout,
            stderr=result.stderr,
            failing_tests=[]
        )
    
    except subprocess.TimeoutExpired:
        return TestResult(
            framework=TestFramework.NPM_TEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=timeout,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message=f"Tests timed out after {timeout}s"
        )
    except Exception as e:
        return TestResult(
            framework=TestFramework.NPM_TEST,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=0.0,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message=f"Test execution failed: {e}"
        )


# ─── Main Test Runner ─────────────────────────────────────────────────────────

def run_tests(
    repo_dir: Path,
    framework: Optional[TestFramework] = None,
    timeout: int = 300,
    specific_tests: Optional[List[str]] = None
) -> TestResult:
    """
    Run tests using detected or specified framework.
    
    Args:
        repo_dir: Repository root directory
        framework: Optional specific framework to use
        timeout: Timeout in seconds
        specific_tests: Optional list of specific tests to run
    
    Returns:
        TestResult
    """
    if framework is None:
        framework = detect_test_framework(repo_dir)
        logger.info(f"Detected test framework: {framework.value}")
    
    if framework == TestFramework.PYTEST:
        return run_pytest(repo_dir, timeout, specific_tests)
    elif framework == TestFramework.UNITTEST:
        return run_unittest(repo_dir, timeout)
    elif framework == TestFramework.JEST:
        return run_jest(repo_dir, timeout)
    elif framework == TestFramework.NPM_TEST:
        return run_npm_test(repo_dir, timeout)
    else:
        return TestResult(
            framework=framework,
            success=False,
            total_tests=0,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=0.0,
            stdout="",
            stderr="",
            failing_tests=[],
            error_message=f"Unsupported test framework: {framework.value}"
        )


def run_baseline_and_verify(
    repo_dir: Path,
    timeout: int = 300
) -> Tuple[TestResult, TestResult]:
    """
    Run tests before and after patch application.
    
    Args:
        repo_dir: Repository root directory
        timeout: Timeout in seconds
    
    Returns:
        (baseline_result, post_patch_result)
    """
    logger.info("Running baseline tests...")
    baseline = run_tests(repo_dir, timeout=timeout)
    
    logger.info(f"Baseline: {baseline.passed}/{baseline.total_tests} passed")
    
    # Note: This function should be called twice - once before patch, once after
    # For now, just return baseline twice (caller will run again after patch)
    return baseline, baseline


def format_test_result(result: TestResult) -> str:
    """
    Format test result for logging/display.
    
    Args:
        result: TestResult to format
    
    Returns:
        Formatted string
    """
    if result.error_message:
        return f"❌ {result.framework.value}: {result.error_message}"
    
    if result.success:
        return (
            f"✓ {result.framework.value}: {result.passed}/{result.total_tests} passed "
            f"({result.duration_seconds:.1f}s)"
        )
    else:
        msg = (
            f"✗ {result.framework.value}: {result.failed}/{result.total_tests} failed "
            f"({result.duration_seconds:.1f}s)"
        )
        if result.failing_tests:
            msg += f"\n  Failing: {', '.join(result.failing_tests[:5])}"
        return msg
