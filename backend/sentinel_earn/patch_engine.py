"""
patch_engine.py — Deterministic patch application for Sentinel Earn
Replaces fragile string-based search/replace with strict JSON patches
Implements exact-match validation, fuzzy fallback, and atomic rollback
"""
import re
import logging
import difflib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class PatchChange:
    """Single search/replace change within a file."""
    description: str
    search: str  # Exact text to find
    replace: str  # Replacement text
    line_hint: Optional[int] = None  # Optional line number hint


@dataclass
class FilePatch:
    """Patch for a single file."""
    path: str
    action: str  # "modify", "create", "delete"
    changes: List[PatchChange]


@dataclass
class PatchResult:
    """Result of applying a patch."""
    success: bool
    files_modified: List[str]
    files_created: List[str]
    files_deleted: List[str]
    errors: List[str]
    diff_preview: str


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_patch_json(patch_data: Dict) -> Tuple[bool, List[str]]:
    """
    Validate patch JSON structure.
    
    Args:
        patch_data: Parsed JSON from model output
    
    Returns:
        (is_valid: bool, errors: List[str])
    """
    errors = []
    
    if "fix" not in patch_data:
        errors.append("Missing 'fix' key in patch data")
        return False, errors
    
    fix = patch_data["fix"]
    
    if "files" not in fix:
        errors.append("Missing 'files' array in fix")
        return False, errors
    
    if not isinstance(fix["files"], list):
        errors.append("'files' must be an array")
        return False, errors
    
    for i, file_patch in enumerate(fix["files"]):
        if "path" not in file_patch:
            errors.append(f"File patch {i}: missing 'path'")
        
        if "action" not in file_patch:
            errors.append(f"File patch {i}: missing 'action'")
        elif file_patch["action"] not in ("modify", "create", "delete"):
            errors.append(f"File patch {i}: invalid action '{file_patch['action']}'")
        
        if file_patch.get("action") != "delete":
            if "changes" not in file_patch:
                errors.append(f"File patch {i}: missing 'changes' array")
            elif not isinstance(file_patch["changes"], list):
                errors.append(f"File patch {i}: 'changes' must be an array")
            else:
                for j, change in enumerate(file_patch["changes"]):
                    if "new_code" not in change:
                        errors.append(f"File patch {i}, change {j}: missing 'new_code'")
    
    return len(errors) == 0, errors


def validate_patch_size(patch_data: Dict, max_files: int = 10, max_lines_per_change: int = 100) -> Tuple[bool, List[str]]:
    """
    Validate patch size to prevent hallucinated full-file rewrites.
    
    Args:
        patch_data: Parsed JSON from model output
        max_files: Maximum number of files to modify
        max_lines_per_change: Maximum lines per individual change
    
    Returns:
        (is_valid: bool, warnings: List[str])
    """
    warnings = []
    
    files = patch_data.get("fix", {}).get("files", [])
    
    if len(files) > max_files:
        warnings.append(f"Too many files ({len(files)} > {max_files}) - possible hallucination")
        return False, warnings
    
    for file_patch in files:
        changes = file_patch.get("changes", [])
        
        for change in changes:
            new_code = change.get("new_code", "")
            line_count = new_code.count("\n") + 1
            
            if line_count > max_lines_per_change:
                warnings.append(
                    f"Change in {file_patch['path']} is too large "
                    f"({line_count} lines > {max_lines_per_change}) - possible full-file rewrite"
                )
                return False, warnings
    
    return True, warnings


# ─── Exact Match Application ──────────────────────────────────────────────────

def apply_exact_match(content: str, search: str, replace: str) -> Tuple[bool, str]:
    """
    Apply patch using exact string match.
    
    Args:
        content: Original file content
        search: Exact text to find
        replace: Replacement text
    
    Returns:
        (success: bool, new_content: str)
    """
    if not search:
        # Pure addition (append)
        return True, content + replace
    
    if search in content:
        # Exact match found - replace first occurrence
        new_content = content.replace(search, replace, 1)
        return True, new_content
    
    return False, content


# ─── Fuzzy Match Fallback ─────────────────────────────────────────────────────

def normalize_whitespace(text: str) -> str:
    """Normalize whitespace for fuzzy matching."""
    # Collapse multiple spaces/tabs to single space
    text = re.sub(r'[ \t]+', ' ', text)
    # Normalize line endings
    text = text.replace('\r\n', '\n')
    # Strip trailing whitespace from each line
    lines = [line.rstrip() for line in text.split('\n')]
    return '\n'.join(lines)


def apply_fuzzy_match(content: str, search: str, replace: str, threshold: float = 0.85) -> Tuple[bool, str]:
    """
    Apply patch using fuzzy matching (whitespace-normalized).
    
    Args:
        content: Original file content
        search: Text to find (will be normalized)
        replace: Replacement text
        threshold: Similarity threshold (0.0-1.0)
    
    Returns:
        (success: bool, new_content: str)
    """
    if not search:
        return False, content
    
    # Normalize search pattern
    norm_search = normalize_whitespace(search)
    
    # Split content into lines for better matching
    lines = content.split('\n')
    search_lines = norm_search.split('\n')
    search_len = len(search_lines)
    
    best_match_idx = -1
    best_similarity = 0.0
    
    # Sliding window to find best match
    for i in range(len(lines) - search_len + 1):
        window = '\n'.join(lines[i:i + search_len])
        norm_window = normalize_whitespace(window)
        
        # Calculate similarity
        similarity = difflib.SequenceMatcher(None, norm_search, norm_window).ratio()
        
        if similarity > best_similarity:
            best_similarity = similarity
            best_match_idx = i
    
    if best_similarity >= threshold and best_match_idx >= 0:
        # Found fuzzy match - replace
        logger.info(f"Fuzzy match found (similarity: {best_similarity:.2f})")
        
        # Preserve original indentation
        original_block = '\n'.join(lines[best_match_idx:best_match_idx + search_len])
        
        # Replace the block
        new_lines = lines[:best_match_idx] + [replace] + lines[best_match_idx + search_len:]
        new_content = '\n'.join(new_lines)
        
        return True, new_content
    
    return False, content


# ─── Diff Generation ──────────────────────────────────────────────────────────

def generate_diff(original: str, modified: str, filename: str = "file") -> str:
    """
    Generate unified diff for preview.
    
    Args:
        original: Original content
        modified: Modified content
        filename: Filename for diff header
    
    Returns:
        Unified diff string
    """
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    
    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm=''
    )
    
    return ''.join(diff)


# ─── Atomic Patch Application ─────────────────────────────────────────────────

def apply_patches_atomic(
    repo_dir: Path,
    patch_data: Dict,
    use_fuzzy: bool = True,
    fuzzy_threshold: float = 0.85
) -> PatchResult:
    """
    Apply all patches atomically with full rollback on any failure.
    
    Args:
        repo_dir: Repository root directory
        patch_data: Validated patch JSON
        use_fuzzy: Enable fuzzy matching fallback
        fuzzy_threshold: Similarity threshold for fuzzy matching
    
    Returns:
        PatchResult with success status and details
    """
    # Validate structure
    is_valid, errors = validate_patch_json(patch_data)
    if not is_valid:
        return PatchResult(
            success=False,
            files_modified=[],
            files_created=[],
            files_deleted=[],
            errors=errors,
            diff_preview=""
        )
    
    # Validate size
    is_valid, warnings = validate_patch_size(patch_data)
    if not is_valid:
        return PatchResult(
            success=False,
            files_modified=[],
            files_created=[],
            files_deleted=[],
            errors=warnings,
            diff_preview=""
        )
    
    files = patch_data["fix"]["files"]
    
    # Snapshot originals for rollback
    originals: Dict[Path, Optional[str]] = {}
    files_modified = []
    files_created = []
    files_deleted = []
    errors = []
    diff_parts = []
    
    try:
        # Phase 1: Snapshot all files
        for file_patch in files:
            file_path = repo_dir / file_patch["path"]
            
            # Validate path is within repo
            try:
                file_path.resolve().relative_to(repo_dir.resolve())
            except ValueError:
                raise ValueError(f"Path traversal detected: {file_patch['path']}")
            
            if file_path.exists():
                originals[file_path] = file_path.read_text(encoding="utf-8", errors="replace")
            else:
                originals[file_path] = None
        
        # Phase 2: Apply all patches
        for file_patch in files:
            file_path = repo_dir / file_patch["path"]
            action = file_patch["action"]
            
            if action == "delete":
                if file_path.exists():
                    file_path.unlink()
                    files_deleted.append(file_patch["path"])
                    logger.info(f"Deleted: {file_patch['path']}")
                continue
            
            # Read current content (or empty for new files)
            if file_path.exists():
                current_content = file_path.read_text(encoding="utf-8", errors="replace")
            else:
                current_content = ""
                file_path.parent.mkdir(parents=True, exist_ok=True)
            
            original_content = current_content
            
            # Apply each change in sequence
            for change in file_patch.get("changes", []):
                search = change.get("old_code", "")
                replace = change.get("new_code", "")
                
                # Try exact match first
                success, current_content = apply_exact_match(current_content, search, replace)
                
                if not success and use_fuzzy and search:
                    # Fallback to fuzzy match
                    logger.info(f"Exact match failed for {file_patch['path']}, trying fuzzy match...")
                    success, current_content = apply_fuzzy_match(
                        current_content, search, replace, fuzzy_threshold
                    )
                
                if not success and search:
                    raise ValueError(
                        f"Could not find search pattern in {file_patch['path']}:\n"
                        f"  Looking for: {search[:100]!r}..."
                    )
            
            # Write modified content
            file_path.write_text(current_content, encoding="utf-8")
            
            if action == "create":
                files_created.append(file_patch["path"])
                logger.info(f"Created: {file_patch['path']}")
            else:
                files_modified.append(file_patch["path"])
                logger.info(f"Modified: {file_patch['path']}")
            
            # Generate diff
            if original_content != current_content:
                diff = generate_diff(original_content, current_content, file_patch["path"])
                diff_parts.append(diff)
        
        # Success!
        logger.info(f"✓ Applied {len(files)} file patch(es) successfully")
        
        return PatchResult(
            success=True,
            files_modified=files_modified,
            files_created=files_created,
            files_deleted=files_deleted,
            errors=[],
            diff_preview="\n".join(diff_parts)
        )
    
    except Exception as e:
        # Rollback all changes
        logger.error(f"Patch application failed: {e}")
        logger.info("Rolling back all changes...")
        
        for file_path, original_content in originals.items():
            try:
                if original_content is None:
                    # File didn't exist - delete it
                    if file_path.exists():
                        file_path.unlink()
                else:
                    # Restore original content
                    file_path.write_text(original_content, encoding="utf-8")
            except Exception as rollback_err:
                logger.error(f"Rollback error for {file_path}: {rollback_err}")
        
        return PatchResult(
            success=False,
            files_modified=[],
            files_created=[],
            files_deleted=[],
            errors=[str(e)],
            diff_preview=""
        )


# ─── Duplicate Patch Detection ────────────────────────────────────────────────

def detect_duplicate_patches(patch_data: Dict) -> List[str]:
    """
    Detect if the same file is patched multiple times (potential hallucination).
    
    Args:
        patch_data: Patch JSON
    
    Returns:
        List of warnings
    """
    warnings = []
    files = patch_data.get("fix", {}).get("files", [])
    
    seen_paths = set()
    for file_patch in files:
        path = file_patch["path"]
        if path in seen_paths:
            warnings.append(f"Duplicate patch for {path} - possible hallucination")
        seen_paths.add(path)
    
    return warnings


# ─── Patch Preview ────────────────────────────────────────────────────────────

def preview_patches(patch_data: Dict) -> str:
    """
    Generate human-readable preview of patches.
    
    Args:
        patch_data: Patch JSON
    
    Returns:
        Preview string
    """
    lines = []
    files = patch_data.get("fix", {}).get("files", [])
    
    lines.append(f"Patch Preview ({len(files)} file(s)):")
    lines.append("=" * 60)
    
    for file_patch in files:
        path = file_patch["path"]
        action = file_patch["action"]
        
        lines.append(f"\n[{action.upper()}] {path}")
        
        if action == "delete":
            lines.append("  (file will be deleted)")
            continue
        
        changes = file_patch.get("changes", [])
        lines.append(f"  {len(changes)} change(s):")
        
        for i, change in enumerate(changes, 1):
            desc = change.get("description", "No description")
            search = change.get("old_code", "")
            replace = change.get("new_code", "")
            
            lines.append(f"\n  Change {i}: {desc}")
            
            if search:
                search_preview = search[:80].replace("\n", "\\n")
                lines.append(f"    - Search:  {search_preview!r}...")
            
            replace_preview = replace[:80].replace("\n", "\\n")
            lines.append(f"    + Replace: {replace_preview!r}...")
    
    return "\n".join(lines)
