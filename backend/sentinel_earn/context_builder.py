"""
context_builder.py — Smart context extraction for Sentinel Earn
AST-based symbol traversal, stack trace parsing, test detection
Replaces naive keyword matching with intelligent code analysis
"""
import ast
import re
import logging
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

logger = logging.getLogger(__name__)


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class Symbol:
    """Represents a code symbol (function, class, variable)."""
    name: str
    type: str  # "function", "class", "method", "variable", "import"
    file: str
    line: int
    context: str  # Surrounding code snippet


@dataclass
class FileContext:
    """Context information for a single file."""
    path: str
    relevance_score: float
    symbols: List[Symbol]
    imports: List[str]
    is_test: bool
    content: str
    compressed_content: Optional[str] = None


# ─── Stack Trace Parsing ──────────────────────────────────────────────────────

def parse_stack_trace(text: str) -> List[Tuple[str, int]]:
    """
    Extract file paths and line numbers from stack traces.
    
    Supports Python, JavaScript, TypeScript stack trace formats.
    
    Args:
        text: Issue body or comment text
    
    Returns:
        List of (filepath, line_number) tuples
    """
    results = []
    
    # Python traceback: File "path/to/file.py", line 42
    python_pattern = r'File\s+"([^"]+)",\s+line\s+(\d+)'
    for match in re.finditer(python_pattern, text):
        filepath = match.group(1)
        line_num = int(match.group(2))
        results.append((filepath, line_num))
    
    # JavaScript/TypeScript: at functionName (path/to/file.js:42:10)
    js_pattern = r'at\s+(?:\w+\s+)?\(([^:]+):(\d+):\d+\)'
    for match in re.finditer(js_pattern, text):
        filepath = match.group(1)
        line_num = int(match.group(2))
        results.append((filepath, line_num))
    
    # Generic: path/to/file.ext:line:col
    generic_pattern = r'([a-zA-Z0-9_/\\.-]+\.(py|js|ts|tsx|jsx)):(\d+)'
    for match in re.finditer(generic_pattern, text):
        filepath = match.group(1)
        line_num = int(match.group(3))
        results.append((filepath, line_num))
    
    return results


def extract_mentioned_files(text: str) -> List[str]:
    """
    Extract file paths mentioned in issue text.
    
    Args:
        text: Issue title + body + comments
    
    Returns:
        List of file paths
    """
    files = []
    
    # Code blocks with file paths
    code_block_pattern = r'```(?:\w+)?\s*\n([^`]+)\n```'
    for match in re.finditer(code_block_pattern, text, re.DOTALL):
        block = match.group(1)
        # Look for file paths in code blocks
        path_pattern = r'([a-zA-Z0-9_/\\.-]+\.(py|js|ts|tsx|jsx|json|yaml|yml|md))'
        files.extend(re.findall(path_pattern, block))
    
    # Inline code with file paths: `path/to/file.py`
    inline_pattern = r'`([a-zA-Z0-9_/\\.-]+\.(py|js|ts|tsx|jsx|json|yaml|yml|md))`'
    files.extend(re.findall(inline_pattern, text))
    
    # Plain mentions
    plain_pattern = r'\b([a-zA-Z0-9_/\\.-]+\.(py|js|ts|tsx|jsx))\b'
    files.extend(re.findall(plain_pattern, text))
    
    # Return unique file paths (just the path, not the extension tuple)
    return list(set(f[0] if isinstance(f, tuple) else f for f in files))


# ─── AST-Based Symbol Extraction ──────────────────────────────────────────────

class PythonSymbolExtractor(ast.NodeVisitor):
    """Extract symbols from Python AST."""
    
    def __init__(self, filepath: str, content: str):
        self.filepath = filepath
        self.content = content
        self.lines = content.split('\n')
        self.symbols: List[Symbol] = []
        self.imports: List[str] = []
    
    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Extract function definitions."""
        context = self._get_context(node.lineno, 3)
        self.symbols.append(Symbol(
            name=node.name,
            type="function",
            file=self.filepath,
            line=node.lineno,
            context=context
        ))
        self.generic_visit(node)
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """Extract async function definitions."""
        context = self._get_context(node.lineno, 3)
        self.symbols.append(Symbol(
            name=node.name,
            type="function",
            file=self.filepath,
            line=node.lineno,
            context=context
        ))
        self.generic_visit(node)
    
    def visit_ClassDef(self, node: ast.ClassDef):
        """Extract class definitions."""
        context = self._get_context(node.lineno, 3)
        self.symbols.append(Symbol(
            name=node.name,
            type="class",
            file=self.filepath,
            line=node.lineno,
            context=context
        ))
        self.generic_visit(node)
    
    def visit_Import(self, node: ast.Import):
        """Extract import statements."""
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Extract from...import statements."""
        if node.module:
            self.imports.append(node.module)
        self.generic_visit(node)
    
    def _get_context(self, line: int, radius: int = 3) -> str:
        """Get surrounding lines for context."""
        start = max(0, line - radius - 1)
        end = min(len(self.lines), line + radius)
        return '\n'.join(self.lines[start:end])


def extract_python_symbols(filepath: str, content: str) -> Tuple[List[Symbol], List[str]]:
    """
    Extract symbols from Python file using AST.
    
    Args:
        filepath: File path for reference
        content: File content
    
    Returns:
        (symbols, imports)
    """
    try:
        tree = ast.parse(content)
        extractor = PythonSymbolExtractor(filepath, content)
        extractor.visit(tree)
        return extractor.symbols, extractor.imports
    except SyntaxError as e:
        logger.warning(f"Syntax error in {filepath}: {e}")
        return [], []
    except Exception as e:
        logger.warning(f"Failed to parse {filepath}: {e}")
        return [], []


def extract_js_symbols_regex(filepath: str, content: str) -> Tuple[List[Symbol], List[str]]:
    """
    Extract symbols from JavaScript/TypeScript using regex (fallback).
    
    Args:
        filepath: File path for reference
        content: File content
    
    Returns:
        (symbols, imports)
    """
    symbols = []
    imports = []
    lines = content.split('\n')
    
    # Function declarations: function name(...) or const name = (...) =>
    func_patterns = [
        r'function\s+(\w+)\s*\(',
        r'const\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>',
        r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(',
    ]
    
    for i, line in enumerate(lines, 1):
        for pattern in func_patterns:
            match = re.search(pattern, line)
            if match:
                name = match.group(1)
                context = '\n'.join(lines[max(0, i-3):min(len(lines), i+3)])
                symbols.append(Symbol(
                    name=name,
                    type="function",
                    file=filepath,
                    line=i,
                    context=context
                ))
    
    # Class declarations
    class_pattern = r'class\s+(\w+)'
    for i, line in enumerate(lines, 1):
        match = re.search(class_pattern, line)
        if match:
            name = match.group(1)
            context = '\n'.join(lines[max(0, i-3):min(len(lines), i+3)])
            symbols.append(Symbol(
                name=name,
                type="class",
                file=filepath,
                line=i,
                context=context
            ))
    
    # Imports: import ... from '...' or require('...')
    import_patterns = [
        r'import\s+.*\s+from\s+[\'"]([^\'"]+)[\'"]',
        r'require\([\'"]([^\'"]+)[\'"]\)',
    ]
    
    for line in lines:
        for pattern in import_patterns:
            matches = re.findall(pattern, line)
            imports.extend(matches)
    
    return symbols, imports


# ─── Test File Detection ──────────────────────────────────────────────────────

def is_test_file(filepath: str, content: str) -> bool:
    """
    Detect if a file is a test file.
    
    Args:
        filepath: File path
        content: File content
    
    Returns:
        True if test file, False otherwise
    """
    path_lower = filepath.lower()
    
    # Path-based detection
    test_indicators = [
        'test_', '_test.', 'tests/', '/test/', 'spec.', '.spec.',
        '__tests__/', 'test.', '.test.', 'testing/'
    ]
    
    if any(indicator in path_lower for indicator in test_indicators):
        return True
    
    # Content-based detection (Python)
    if filepath.endswith('.py'):
        if 'import pytest' in content or 'import unittest' in content:
            return True
        if re.search(r'def\s+test_\w+', content):
            return True
    
    # Content-based detection (JavaScript/TypeScript)
    if filepath.endswith(('.js', '.ts', '.jsx', '.tsx')):
        if any(framework in content for framework in ['describe(', 'it(', 'test(', 'expect(']):
            return True
    
    return False


# ─── Relevance Scoring ────────────────────────────────────────────────────────

def score_file_relevance(
    filepath: str,
    content: str,
    symbols: List[Symbol],
    imports: List[str],
    mentioned_files: Set[str],
    stack_trace_files: Set[str],
    keywords: Set[str]
) -> float:
    """
    Score file relevance based on multiple factors.
    
    Args:
        filepath: File path
        content: File content
        symbols: Extracted symbols
        imports: Extracted imports
        mentioned_files: Files mentioned in issue
        stack_trace_files: Files from stack traces
        keywords: Keywords from issue
    
    Returns:
        Relevance score (0.0-10.0)
    """
    score = 0.0
    
    # Direct mention in issue (highest priority)
    if any(mentioned in filepath for mentioned in mentioned_files):
        score += 5.0
    
    # In stack trace (very high priority)
    if any(trace_file in filepath for trace_file in stack_trace_files):
        score += 4.0
    
    # Symbol name matches keywords
    symbol_names = {s.name.lower() for s in symbols}
    keyword_matches = symbol_names & keywords
    score += min(len(keyword_matches) * 0.5, 3.0)
    
    # File name contains keywords
    filename = Path(filepath).stem.lower()
    if any(kw in filename for kw in keywords):
        score += 2.0
    
    # Content contains keywords (case-insensitive)
    content_lower = content.lower()
    content_matches = sum(1 for kw in keywords if kw in content_lower)
    score += min(content_matches * 0.2, 2.0)
    
    # Test files get bonus (important for verification)
    if is_test_file(filepath, content):
        score += 1.5
    
    # Small files are easier to include
    line_count = content.count('\n') + 1
    if line_count < 100:
        score += 0.5
    
    return min(score, 10.0)


# ─── Context Compression ──────────────────────────────────────────────────────

def compress_file_content(
    content: str,
    symbols: List[Symbol],
    max_lines: int = 150
) -> str:
    """
    Compress file content intelligently.
    
    Strategy:
    - Keep all symbol definitions (functions, classes)
    - Keep first 30 and last 30 lines
    - Omit middle if file is too long
    
    Args:
        content: Original file content
        symbols: Extracted symbols
        max_lines: Maximum lines to keep
    
    Returns:
        Compressed content
    """
    lines = content.split('\n')
    
    if len(lines) <= max_lines:
        return content
    
    # Collect important line numbers (symbol definitions)
    important_lines = set()
    for symbol in symbols:
        # Include symbol line and 2 lines before/after
        for offset in range(-2, 3):
            line_num = symbol.line + offset - 1  # Convert to 0-indexed
            if 0 <= line_num < len(lines):
                important_lines.add(line_num)
    
    # Always include first 30 and last 30 lines
    head_lines = set(range(min(30, len(lines))))
    tail_lines = set(range(max(0, len(lines) - 30), len(lines)))
    
    keep_lines = important_lines | head_lines | tail_lines
    
    # Build compressed content
    result = []
    last_kept = -2
    
    for i in range(len(lines)):
        if i in keep_lines:
            if i > last_kept + 1:
                omitted = i - last_kept - 1
                result.append(f"\n# ... [{omitted} lines omitted] ...\n")
            result.append(lines[i])
            last_kept = i
    
    return '\n'.join(result)


# ─── Main Context Builder ─────────────────────────────────────────────────────

def build_context(
    repo_dir: Path,
    issue_text: str,
    language: str,
    max_files: int = 15,
    max_total_tokens: int = 8000
) -> Dict[str, FileContext]:
    """
    Build intelligent context from repository.
    
    Args:
        repo_dir: Repository root directory
        issue_text: Combined issue title + body + comments
        language: Primary language (python, javascript, typescript)
        max_files: Maximum number of files to include
        max_total_tokens: Maximum total tokens (~4 chars per token)
    
    Returns:
        Dictionary of filepath -> FileContext
    """
    # Extract hints from issue
    stack_traces = parse_stack_trace(issue_text)
    mentioned_files = extract_mentioned_files(issue_text)
    
    stack_trace_files = {st[0] for st in stack_traces}
    mentioned_file_set = set(mentioned_files)
    
    # Extract keywords (simple for now)
    keywords = set()
    words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b', issue_text.lower())
    stopwords = {'the', 'this', 'that', 'with', 'from', 'have', 'been', 'were', 'will', 'would', 'could', 'should'}
    keywords = {w for w in words if w not in stopwords}
    
    # Determine file extensions
    ext_map = {
        'python': ['.py'],
        'javascript': ['.js', '.mjs', '.cjs', '.jsx'],
        'typescript': ['.ts', '.tsx'],
    }
    extensions = ext_map.get(language.lower(), ['.py', '.js', '.ts'])
    
    # Skip directories
    skip_dirs = {
        'node_modules', '.git', '__pycache__', 'dist', 'build',
        '.tox', 'venv', '.venv', 'coverage', '.pytest_cache',
        '.next', '.nuxt', 'vendor', 'target'
    }
    
    # Scan repository
    file_contexts: Dict[str, FileContext] = {}
    
    for ext in extensions:
        for filepath in repo_dir.rglob(f'*{ext}'):
            # Skip excluded directories
            if any(skip_dir in filepath.parts for skip_dir in skip_dirs):
                continue
            
            # Skip if too large
            if filepath.stat().st_size > 100_000:  # 100KB limit
                continue
            
            try:
                content = filepath.read_text(encoding='utf-8', errors='replace')
                rel_path = str(filepath.relative_to(repo_dir))
                
                # Extract symbols
                if ext == '.py':
                    symbols, imports = extract_python_symbols(rel_path, content)
                else:
                    symbols, imports = extract_js_symbols_regex(rel_path, content)
                
                # Score relevance
                relevance = score_file_relevance(
                    rel_path, content, symbols, imports,
                    mentioned_file_set, stack_trace_files, keywords
                )
                
                # Detect if test file
                is_test = is_test_file(rel_path, content)
                
                file_contexts[rel_path] = FileContext(
                    path=rel_path,
                    relevance_score=relevance,
                    symbols=symbols,
                    imports=imports,
                    is_test=is_test,
                    content=content
                )
                
            except Exception as e:
                logger.warning(f"Failed to process {filepath}: {e}")
                continue
    
    # Sort by relevance and take top N
    sorted_files = sorted(
        file_contexts.items(),
        key=lambda x: x[1].relevance_score,
        reverse=True
    )[:max_files]
    
    # Compress content to fit token budget
    max_chars = max_total_tokens * 4
    current_chars = 0
    result = {}
    
    for filepath, ctx in sorted_files:
        compressed = compress_file_content(ctx.content, ctx.symbols)
        ctx.compressed_content = compressed
        
        if current_chars + len(compressed) > max_chars:
            # Try to fit at least the symbols
            symbol_summary = '\n'.join(
                f"Line {s.line}: {s.type} {s.name}" for s in ctx.symbols[:10]
            )
            if current_chars + len(symbol_summary) <= max_chars:
                ctx.compressed_content = f"# {filepath}\n# (content truncated, symbols only)\n{symbol_summary}"
                current_chars += len(ctx.compressed_content)
                result[filepath] = ctx
            break
        
        current_chars += len(compressed)
        result[filepath] = ctx
    
    logger.info(
        f"Built context: {len(result)} files, {current_chars} chars, "
        f"~{current_chars // 4} tokens"
    )
    
    return result


def format_context_for_prompt(contexts: Dict[str, FileContext]) -> str:
    """
    Format file contexts for inclusion in prompt.
    
    Args:
        contexts: Dictionary of filepath -> FileContext
    
    Returns:
        Formatted string for prompt
    """
    parts = []
    
    for filepath, ctx in sorted(contexts.items(), key=lambda x: x[1].relevance_score, reverse=True):
        parts.append(f"\n### FILE: {filepath}")
        parts.append(f"# Relevance: {ctx.relevance_score:.1f}/10")
        parts.append(f"# Test file: {ctx.is_test}")
        parts.append(f"# Symbols: {len(ctx.symbols)}")
        
        if ctx.symbols:
            symbol_list = ', '.join(f"{s.type}:{s.name}" for s in ctx.symbols[:5])
            parts.append(f"# Key symbols: {symbol_list}")
        
        parts.append("```")
        parts.append(ctx.compressed_content or ctx.content)
        parts.append("```\n")
    
    return '\n'.join(parts)
