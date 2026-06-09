"""
prompt_engine.py — Advanced Ollama prompt engineering for Sentinel Earn
Implements 8 techniques to maximise qwen2.5-coder:14b output quality

Techniques:
  1. Chain-of-thought forcing
  2. Role priming
  3. Structured output enforcement + schema validation
  4. Context compression (token budget aware)
  5. Self-verification loop
  6. Retry with simplification
  7. Language-specific instructions
  8. Complexity gating
"""
import json
import re
import time
import logging
from typing import Optional, Dict, List
import httpx
from dotenv import load_dotenv
import os

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434") + "/api/generate"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:14b")
MAX_CODE_CHARS = 8000 * 4  # ~8 000 tokens × 4 chars/token

logger = logging.getLogger(__name__)


# ─── 2. Role Priming ──────────────────────────────────────────────────────────

ROLE_PRIMER = (
    "You are a senior software engineer with 15 years of experience. "
    "You have reviewed thousands of bug reports and your fixes are precise, minimal, "
    "and always include your reasoning. "
    "You never guess — if you are not confident, you say so clearly. "
    "You write clean, idiomatic code that follows the language's best practices. "
    "You prefer surgical, minimal changes over rewrites."
)


# ─── 7. Language-Specific Instructions ───────────────────────────────────────

LANG_RULES: Dict[str, str] = {
    "python": (
        "Python-specific rules:\n"
        "- Follow PEP 8 strictly.\n"
        "- Use type hints for all function signatures.\n"
        "- No global mutable variables.\n"
        "- Use f-strings for string formatting.\n"
        "- Prefer pathlib over os.path.\n"
        "- Use context managers for file/resource handling."
    ),
    "javascript": (
        "JavaScript-specific rules:\n"
        "- Use const/let, never var.\n"
        "- Handle all promise rejections with .catch() or try/catch.\n"
        "- Use optional chaining (?.) for nullable access.\n"
        "- Prefer arrow functions for callbacks.\n"
        "- Use template literals for string interpolation.\n"
        "- Use === not ==.\n"
        "- Destructure where it improves clarity."
    ),
    "typescript": (
        "TypeScript-specific rules:\n"
        "- Maintain strict typing throughout.\n"
        "- No 'any' types — use 'unknown' then narrow if uncertain.\n"
        "- Define interfaces for all object shapes.\n"
        "- Use type guards for runtime checks.\n"
        "- Export types alongside functions.\n"
        "- Prefer readonly for immutable props."
    ),
}


# ─── 4. Context Compression ──────────────────────────────────────────────────

def compress_context(files: Dict[str, str], issue_keywords: List[str]) -> str:
    """
    Compress file context intelligently to fit within MAX_CODE_CHARS.
    - Only files that are actually relevant (keyword hit or small enough).
    - Strip blank lines.
    - Long files: first 50 + last 50 + up to 30 keyword-containing lines.
    - Hard stop at MAX_CODE_CHARS total.
    """
    kws = [k.lower() for k in issue_keywords if len(k) > 2]
    parts: List[str] = []
    budget = MAX_CODE_CHARS

    for filename, content in files.items():
        if not content or not content.strip():
            continue
        if budget <= 0:
            parts.append(f"\n### FILE: {filename}\n[Budget exhausted — omitted]\n")
            continue

        lines = [ln for ln in content.split("\n") if ln.strip()]  # strip blank lines

        # Find keyword-containing lines
        kw_lines: List[tuple] = []
        for i, ln in enumerate(lines):
            if any(kw in ln.lower() for kw in kws):
                kw_lines.append((i, ln))

        if len(lines) <= 120:
            body = "\n".join(lines)
        else:
            head = lines[:50]
            tail = lines[-50:]
            head_tail_idx = set(range(50)) | set(range(len(lines) - 50, len(lines)))
            extra = [(i, ln) for i, ln in kw_lines if i not in head_tail_idx][:30]
            omitted = len(lines) - 100 - len(extra)

            sections = ["\n".join(head)]
            if extra:
                sections.append(f"\n# ... [{omitted} lines omitted, {len(extra)} keyword lines below] ...\n")
                sections.extend(f"# L{i+1}: {ln}" for i, ln in extra)
            sections.append(f"\n# ... [tail] ...\n")
            sections.append("\n".join(tail))
            body = "\n".join(sections)

        section = f"\n### FILE: {filename}\n```\n{body}\n```\n"
        budget -= len(section)
        parts.append(section)

    return "\n".join(parts) if parts else "(No relevant source files found)"


# ─── Prompt Builders ─────────────────────────────────────────────────────────

def build_complexity_assessment_prompt(title: str, body: str) -> str:
    """8. Ask model to self-assess complexity before attempting a fix."""
    return f"""{ROLE_PRIMER}

Assess the complexity of fixing this GitHub issue on a scale of 1–10.

Issue Title: {title}
Issue Body:
{body[:2000]}

Complexity scale:
  1–3  straightforward (typo, missing null-check, simple off-by-one)
  4–5  moderate (multi-file change, understand one subsystem)
  6–7  involved (design understanding required)
  8–10 complex (architecture change, perf, security, concurrency)

If complexity > 5, recommend skipping this issue.

Respond with ONLY a valid JSON object — no markdown, no text outside the JSON:
{{
    "complexity": <integer 1-10>,
    "reasoning": "<one sentence>",
    "should_attempt": <true if complexity <= 5, else false>,
    "skip_reason": "<required only when should_attempt is false>"
}}"""


def build_fix_prompt(
    issue: Dict,
    code_context: str,
    language: str,
    signals_block: str = "",
    conventions_block: str = "",
    adaptive_few_shot: str = "",
) -> str:
    """
    Main fix prompt.
    Technique 1 (CoT) + Technique 2 (role) + Technique 3 (structured JSON)
    + Technique 7 (language rules) + structured signals + repo conventions
    + optional adaptive few-shot from RAG.
    """
    lang_rules = LANG_RULES.get(language.lower(), "")
    comments_block = ""
    if issue.get("comments"):
        joined = "\n---\n".join(c[:500] for c in issue["comments"][:5])
        comments_block = f"\nIssue Comments (latest 5):\n{joined}\n"

    return f"""{ROLE_PRIMER}

{lang_rules}
{conventions_block}
You are fixing a GitHub issue. You MUST reason step-by-step BEFORE writing any code.

## Issue
Title: {issue.get("title", "")}
URL:   {issue.get("issue_url", "")}
Body:
{(issue.get("body") or "")[:3000]}
{comments_block}
{signals_block}
## Relevant Code
{code_context}

## Chain of Thought (complete ALL 5 steps before writing code)
1. What is the EXACT root cause of this bug?
2. Which specific files and line numbers are involved?
3. What is the MINIMAL change that fixes it?
4. What could go wrong with this fix?
5. How would you verify it works?

## Output — STRICT JSON
You MUST respond with ONLY a valid JSON object. No markdown. No explanation outside the JSON.
If you cannot fix this with confidence >= 7, set confidence < 7 and explain in diagnosis.

{{
    "chain_of_thought": {{
        "root_cause":    "<exact root cause>",
        "files_involved": ["<file1>", "<file2>"],
        "minimal_change": "<description of the minimal fix>",
        "risks":         "<potential issues with this fix>",
        "verification":  "<how to verify it works>"
    }},
    "diagnosis":  "<concise technical explanation>",
    "fix": {{
        "files": [
            {{
                "path":   "<relative file path>",
                "action": "modify|create|delete",
                "changes": [
                    {{
                        "description": "<what this change does>",
                        "old_code":    "<exact code to replace — empty string for pure additions>",
                        "new_code":    "<replacement code>"
                    }}
                ]
            }}
        ]
    }},
    "confidence":  <integer 0-10>,
    "explanation": "<brief fix description for PR body>"
}}

## EXAMPLE — follow this structure exactly
{{
    "chain_of_thought": {{
        "root_cause":    "The user_id field is never assigned in signup(), so db.save() persists None.",
        "files_involved": ["auth/signup.py"],
        "minimal_change": "Assign user.user_id = generate_id() before calling db.save(user).",
        "risks":         "generate_id() may raise on entropy exhaustion; caller should handle.",
        "verification":  "Unit-test the signup path and assert user.user_id is not None after save."
    }},
    "diagnosis":  "Missing user_id assignment causes NullPointerError on first login.",
    "fix": {{
        "files": [
            {{
                "path":   "auth/signup.py",
                "action": "modify",
                "changes": [
                    {{
                        "description": "Assign generated user_id before persisting",
                        "old_code":    "db.save(user)",
                        "new_code":    "user.user_id = generate_id()\\ndb.save(user)"
                    }}
                ]
            }}
        ]
    }},
    "confidence":  8,
    "explanation": "Add missing user_id assignment before db.save in signup handler."
}}

{adaptive_few_shot}"""


def build_verification_prompt(issue: Dict, proposed_fix: Dict) -> str:
    """5. Adversarial self-verification — prime the model to find bugs, not approve."""
    fix_json = json.dumps(proposed_fix.get("fix", {}), indent=2)[:2000]
    return f"""{ROLE_PRIMER}

You are a skeptical senior reviewer whose job is to FIND BUGS in proposed fixes.
Assume this fix is WRONG until you can prove otherwise. Your reputation depends on
catching subtle errors before they reach production. Do NOT rubber-stamp this fix.

## Original Issue
Title: {issue.get("title", "")}
Body:  {(issue.get("body") or "")[:1500]}

## Your Proposed Fix
{fix_json}

## Critical Review — answer ALL questions honestly:
1. Does this fix address the root cause (not just a symptom)?
2. Could this fix introduce new bugs or regressions?
3. Is this truly the minimal change needed?
4. Are there edge cases not handled?

Respond with ONLY a valid JSON object, no markdown:
{{
    "addresses_root_cause": <true/false>,
    "introduces_bugs":      <true/false>,
    "bug_description":      "<describe any new bugs, or empty string>",
    "is_minimal":           <true/false>,
    "unhandled_edge_cases": "<list any edge cases, or 'none'>",
    "confidence":           <integer 0-10>,
    "verdict":              "approve|revise",
    "revision_notes":       "<required if verdict is revise>"
}}"""


def build_retry_prompt(issue: Dict, failed_attempt: str, reason: str) -> str:
    """6. Simplification retry — strip away complexity, one-line focus."""
    return f"""{ROLE_PRIMER}

A previous fix attempt failed or had low confidence.
Reason: {reason}

The previous analysis was too complex. Focus ONLY on this:
{issue.get("title", "")}

Issue body (truncated):
{(issue.get("body") or "")[:800]}

Previous attempt (for reference — do NOT repeat its mistakes):
{failed_attempt[:400]}

New instructions:
- What is the ONE line or function that needs to change?
- Make the smallest possible fix that addresses the issue title.
- If you still can't be confident, set confidence < 7 and explain why.

Respond with ONLY a valid JSON object, no markdown:
{{
    "diagnosis":  "<one sentence: what needs to change>",
    "fix": {{
        "files": [
            {{
                "path":   "<file path>",
                "action": "modify",
                "changes": [
                    {{
                        "description": "<what this change does>",
                        "old_code":    "<exact code to replace>",
                        "new_code":    "<replacement code>"
                    }}
                ]
            }}
        ]
    }},
    "confidence":  <integer 0-10>,
    "explanation": "<brief fix description>"
}}"""


# ─── 3. Response Parsing & Validation ────────────────────────────────────────

def parse_and_validate_response(response: str) -> Optional[Dict]:
    """
    Extract and validate JSON from model output.
    Handles markdown fences, leading/trailing prose, partial drift.
    """
    if not response:
        return None

    cleaned = response.strip()

    # Strip markdown code fence if present
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        # Find outermost braces
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
        else:
            logger.debug(f"No JSON object found in response: {response[:100]}")
            return None

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Attempt relaxed parse — remove trailing commas (common model error)
        try:
            fixed = re.sub(r",\s*([\}\]])", r"\1", cleaned)
            data = json.loads(fixed)
        except json.JSONDecodeError:
            logger.warning(f"JSON parse failed: {exc} | snippet: {cleaned[:150]}")
            return None

    # Normalise confidence to int
    if "confidence" in data:
        try:
            data["confidence"] = int(float(data["confidence"]))
        except (ValueError, TypeError):
            data["confidence"] = 0

    # Validate fix structure when present
    if "fix" in data:
        if not isinstance(data["fix"].get("files"), list):
            logger.warning("Fix response missing 'files' list")
            return None

    return data


def _correction_prompt(bad_response: str, original_task_snippet: str) -> str:
    """Schema-correction prompt when model drifts from JSON."""
    return (
        f"Your previous response was not valid JSON.\n"
        f"What you responded (first 300 chars):\n{bad_response[:300]}\n\n"
        f"Original task summary: {original_task_snippet[:200]}\n\n"
        "You MUST respond with ONLY a raw JSON object.\n"
        "No markdown. No ```json fences. No text before or after.\n"
        "Start your response with { and end with }.\n"
        "Provide the same information as before in valid JSON."
    )


# ─── Model Routing ───────────────────────────────────────────────────────────

def get_routed_model(task_type: str = "fix") -> str:
    """Return the best available local model for a task type via ModelRouter."""
    try:
        from workers.sentinel.model_router import get_model_router
        selection = get_model_router().route_for_task(task_type, "", prefer_local=True)
        return selection.get("model") or OLLAMA_MODEL
    except Exception:
        return OLLAMA_MODEL


# ─── Ollama API ───────────────────────────────────────────────────────────────

def call_ollama(
    prompt: str,
    temperature: float = 0.1,
    system: str = "",
    model: str = "",
    response_format: Optional[Dict] = None,
) -> str:
    """
    Single synchronous Ollama call.

    response_format: optional Ollama `format` field — either the string "json"
    or a full JSON schema dict. When provided, Ollama constrains token sampling
    to only produce tokens that match the schema, eliminating JSON drift.
    """
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "num_predict": 4096,
        },
    }
    if system:
        payload["system"] = system
    if response_format is not None:
        payload["format"] = response_format
    from core.providers.inference import infer_text
    return infer_text(
        "earn", prompt, system or "",
        task_kind="agentic", on_demand=True, timeout=180.0, model=model or OLLAMA_MODEL,
    )


def call_ollama_think(prompt: str, system: str = "", model: str = "") -> str:
    """Scratchpad pass — free-form reasoning at higher temperature for exploration."""
    return call_ollama(prompt, temperature=0.35, system=system, model=model)


# JSON schema for the fix-pipeline output — constrains Ollama's token sampling.
FIX_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "chain_of_thought": {
            "type": "object",
            "properties": {
                "root_cause":     {"type": "string"},
                "files_involved": {"type": "array", "items": {"type": "string"}},
                "minimal_change": {"type": "string"},
                "risks":          {"type": "string"},
                "verification":   {"type": "string"},
            },
        },
        "diagnosis": {"type": "string"},
        "fix": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path":   {"type": "string"},
                            "action": {"type": "string"},
                            "changes": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "description": {"type": "string"},
                                        "old_code":    {"type": "string"},
                                        "new_code":    {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
        "confidence":  {"type": "integer"},
        "explanation": {"type": "string"},
    },
    "required": ["diagnosis", "fix", "confidence"],
}


def call_ollama_extract(
    prompt: str,
    system: str = "",
    model: str = "",
    schema: Optional[Dict] = None,
) -> str:
    """
    Extraction pass — JSON formatting only, near-zero temperature for precision.

    When schema is provided (default FIX_RESPONSE_SCHEMA), Ollama constrains
    sampling to only produce schema-valid tokens. Pass schema={} to disable.
    """
    fmt: Optional[Dict] = FIX_RESPONSE_SCHEMA if schema is None else (schema or None)
    return call_ollama(
        prompt,
        temperature=0.05,
        system=system,
        model=model,
        response_format=fmt,
    )


def call_ollama_with_retry(
    prompt: str,
    max_retries: int = 3,
    system: str = "",
    model: str = "",
) -> str:
    """
    Call Ollama with exponential backoff.
    On schema drift tries a correction prompt before giving up.
    """
    last_raw = ""
    last_err: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            raw = call_ollama(prompt, system=system, model=model)
            last_raw = raw

            if raw and "{" in raw:
                parsed = parse_and_validate_response(raw)
                if parsed is not None:
                    return raw  # ✓ Good response

                # Schema drift — inject correction on non-final attempts
                if attempt < max_retries - 1:
                    logger.warning(f"Schema drift (attempt {attempt+1}) — retrying with correction")
                    prompt = _correction_prompt(raw, prompt[:200])
                    time.sleep(2 ** attempt)
                    continue

            # Empty or non-JSON — just retry
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue

            return raw  # Return whatever we have on last attempt

        except httpx.ConnectError as e:
            last_err = e
            logger.error(f"Ollama not reachable at {OLLAMA_URL} — is it running?")
        except httpx.TimeoutException as e:
            last_err = e
            logger.warning(f"Ollama timeout (attempt {attempt+1})")
        except Exception as e:
            last_err = e
            logger.error(f"Ollama error (attempt {attempt+1}): {e}")

        if attempt < max_retries - 1:
            wait = 2 ** attempt
            logger.info(f"Retrying in {wait}s…")
            time.sleep(wait)

    raise RuntimeError(
        f"Ollama failed after {max_retries} attempts. "
        f"Last error: {last_err}. "
        f"Last response snippet: {last_raw[:200]}"
    )


# ─── Public helpers ───────────────────────────────────────────────────────────

def _extract_keywords(text: str) -> List[str]:
    """Extract meaningful identifiers / terms from issue text."""
    stopwords = {
        "the", "a", "an", "is", "it", "in", "on", "at", "to", "for", "of",
        "and", "or", "but", "with", "this", "that", "when", "if", "not",
        "be", "been", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "must", "can", "as",
        "from", "by", "are", "was", "were", "i", "we", "you", "he", "she",
        "they", "my", "your", "our", "its", "issue", "bug", "fix", "error",
        "problem", "please", "help", "need", "want", "get", "set",
    }
    words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", text)
    seen: set = set()
    result: List[str] = []
    for w in words:
        lw = w.lower()
        if lw not in stopwords and lw not in seen:
            result.append(w)
            seen.add(lw)
        if len(result) >= 40:
            break
    return result


# ─── Code Complexity Analysis (radon) ────────────────────────────────────────

def analyze_code_complexity(files: Dict[str, str], language: str) -> Dict:
    """
    Run radon cyclomatic complexity on Python file contents. For non-Python
    languages or when radon is unavailable, return an empty report. Never raises.

    Returns:
      {
        "max_complexity": int,
        "high_complexity_blocks": [{"file": str, "name": str, "complexity": int}],
        "warning": str,   # human-readable warning or ""
      }
    """
    report = {"max_complexity": 0, "high_complexity_blocks": [], "warning": ""}
    if language.lower() != "python":
        return report
    try:
        from radon.complexity import cc_visit  # type: ignore
    except Exception:
        return report

    for path, content in files.items():
        if not content or not path.endswith(".py"):
            continue
        try:
            blocks = cc_visit(content)
        except Exception:
            continue
        for blk in blocks:
            cx = getattr(blk, "complexity", 0)
            if cx > report["max_complexity"]:
                report["max_complexity"] = cx
            if cx >= 12:
                report["high_complexity_blocks"].append({
                    "file": path,
                    "name": getattr(blk, "name", "?"),
                    "complexity": cx,
                })

    if report["high_complexity_blocks"]:
        lines = [
            f"- {b['file']}::{b['name']} (CC={b['complexity']})"
            for b in report["high_complexity_blocks"][:5]
        ]
        report["warning"] = (
            "⚠ High-complexity code involved — be EXTRA careful with side effects, "
            "control flow, and edge cases:\n" + "\n".join(lines)
        )
    return report


# ─── RAG: Past Successful Repairs ─────────────────────────────────────────────

REPAIR_MEMORY_NAMESPACE = "repair_examples"


def retrieve_similar_past_repairs(issue: Dict, limit: int = 2) -> List[Dict]:
    """
    Look up the persistent memory store for past successful repairs whose
    issue text is semantically similar to the current one. Returns [] on
    any failure — purely an enhancement.
    """
    try:
        from memory.persistent_memory import get_memory
        query = f"{issue.get('title', '')}\n{(issue.get('body') or '')[:800]}"
        hits = get_memory().recall(REPAIR_MEMORY_NAMESPACE, query, limit=limit)
        return hits or []
    except Exception as exc:
        logger.debug(f"[rag] retrieval skipped (non-fatal): {exc}")
        return []


def remember_successful_repair(issue: Dict, fix_result: Dict, confidence: int) -> None:
    """
    Persist a high-confidence repair as a future few-shot example.
    Skips silently if memory is unavailable.
    """
    if confidence < 8:
        return
    try:
        from memory.persistent_memory import get_memory
        content = (
            f"ISSUE: {issue.get('title', '')[:200]}\n"
            f"BODY: {(issue.get('body') or '')[:400]}\n"
            f"DIAGNOSIS: {fix_result.get('diagnosis', '')[:300]}\n"
            f"FIX: {json.dumps(fix_result.get('fix', {}))[:800]}"
        )
        metadata = {
            "confidence": confidence,
            "issue_url": issue.get("issue_url", ""),
            "language": issue.get("language", ""),
        }
        get_memory().remember(REPAIR_MEMORY_NAMESPACE, content, metadata)
    except Exception as exc:
        logger.debug(f"[rag] persistence skipped (non-fatal): {exc}")


def format_past_repairs_for_prompt(hits: List[Dict]) -> str:
    if not hits:
        return ""
    blocks = ["## Similar Successful Past Repairs (your own playbook)"]
    for i, hit in enumerate(hits[:2], 1):
        blocks.append(f"\n### Example {i} (similarity={hit.get('score', 0):.2f})\n{hit.get('content', '')[:1200]}")
    return "\n".join(blocks) + "\n"


# ─── Full Fix Pipeline ────────────────────────────────────────────────────────

def assess_complexity(issue: Dict) -> Optional[Dict]:
    """8. Complexity gate — returns parsed result or None on error."""
    prompt = build_complexity_assessment_prompt(
        issue.get("title", ""), issue.get("body", "") or ""
    )
    try:
        raw = call_ollama_with_retry(prompt)
        return parse_and_validate_response(raw)
    except Exception as e:
        logger.error(f"Complexity assessment error: {e}")
        return None


def _generate_fix_candidate(
    issue: Dict,
    code_context: str,
    language: str,
    extra_context: str,
    framing: str,
    active_model: str,
    signals_block: str = "",
    conventions_block: str = "",
    adaptive_few_shot: str = "",
) -> Optional[Dict]:
    """One two-pass fix attempt with a specific framing system prompt."""
    base_prompt = build_fix_prompt(
        issue, code_context, language,
        signals_block=signals_block,
        conventions_block=conventions_block,
        adaptive_few_shot=adaptive_few_shot,
    )
    if extra_context:
        base_prompt = extra_context + "\n\n" + base_prompt
    think_prompt = (
        base_prompt
        + "\n\nWrite your complete analysis in plain English first. "
        "Work through all 5 Chain of Thought steps in prose. "
        "Do NOT produce the JSON yet — just think the problem through."
    )
    system = ROLE_PRIMER + ("\n\n" + framing if framing else "")
    try:
        scratchpad = call_ollama_think(think_prompt, system=system, model=active_model)
        logger.debug(f"Scratchpad {len(scratchpad)} chars: {scratchpad[:120]!r}")

        extract_prompt = (
            "You have analyzed the issue. Here is your reasoning:\n\n"
            + scratchpad[:3000]
            + "\n\nNow output ONLY the JSON object described in the original instructions. "
            "Start with { and end with }. No markdown. No text outside the JSON."
        )
        raw = call_ollama_extract(extract_prompt, system=system, model=active_model)
        result = parse_and_validate_response(raw)
        if result is None and scratchpad:
            result = parse_and_validate_response(scratchpad)
        return result
    except Exception as exc:
        logger.error(f"Fix candidate failed ({framing[:30]}): {exc}")
        return None


TOT_FRAMINGS = [
    "BIAS: Prefer the absolute minimal one-line change. Reject any fix that touches more than one function.",
    "BIAS: Think defensively. Add explicit null/empty/type guards. Handle edge cases first, happy path second.",
    "BIAS: Test-first thinking. Imagine the failing test that proves the bug, then write the change that makes it pass.",
]


def _safe(fn, *args, label: str = "", default=None, **kwargs):
    """Run an optional enhancement; log warnings, never raise."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.warning("[%s] non-fatal: %s", label or fn.__name__, exc)
        return default


def run_fix_pipeline(
    issue: Dict, files: Dict[str, str], language: str, dry_run: bool = False
) -> Optional[Dict]:
    """
    Full fix pipeline with all reasoning enhancements:
      1.  Ollama complexity gate
      2.  Static code complexity (radon)
      3.  Structured signal extraction (stack traces, file refs, errors)
      4.  Repo convention detection
      5.  Context compression
      6.  RAG: similar past SUCCESSFUL repairs
      7.  RAG: similar past FAILED repairs (anti-patterns)
      8.  GitHub: repo-specific maintainer-accepted PR diffs
      9.  Adaptive few-shot from top RAG hit
      10. Two-pass think → schema-constrained extract
      11. Tree-of-Thought (gated to mid-complexity issues)
      12. Simplification retry on low confidence
      13. Adversarial local self-verification
      14. Chain-of-Verification (independent Q&A)
      15. Iterative refinement on revise verdict
      16. Pre-flight patch validation + auto-correction
      17. Cross-model verification (Groq, optional)
      18. Persist outcome to RAG (success → wins store, fail → losses store)

    Every optional enhancement fails silently — the core repair always
    completes even if every enhancement is offline.
    """
    from repair_intelligence import (
        extract_issue_signals, format_signals_for_prompt,
        detect_repo_conventions, format_conventions_for_prompt,
        validate_proposed_patch, build_patch_repair_prompt,
        chain_of_verification,
        remember_failed_repair, retrieve_similar_past_failures, format_past_failures_for_prompt,
        build_adaptive_few_shot,
        classify_issue_intent_local,
        resolve_symbols, format_symbols_for_prompt,
        analyze_caller_impact, format_caller_impact_for_prompt,
        verify_code_grounding,
        detect_test_coverage, format_test_coverage_for_prompt,
        score_diff_minimality,
        ReasoningTrace,
    )

    trace = ReasoningTrace()

    # ── Step 0: Issue intent classification (heuristic, local) ────────────────
    intent_info = _safe(
        classify_issue_intent_local,
        issue.get("title", ""), issue.get("body", "") or "",
        label="intent", default={"intent": "bug", "should_attempt": True, "confidence": 0.0},
    ) or {"intent": "bug", "should_attempt": True, "confidence": 0.0}
    trace.record("intent", intent_info["intent"], confidence=intent_info.get("confidence"))
    if not intent_info.get("should_attempt", True):
        logger.info(
            f"[intent] classified as {intent_info['intent']} "
            f"(conf={intent_info.get('confidence')}) — skipping"
        )
        return {
            "skipped": True,
            "reason": f"Issue intent classified as {intent_info['intent']} (not actionable as a code fix)",
            "intent": intent_info,
            "trace": trace.to_list(),
        }
    logger.info(f"[intent] {intent_info['intent']} (conf={intent_info.get('confidence')})")

    # ── Step 1: Complexity gate ───────────────────────────────────────────────
    logger.info(f"Assessing complexity: {issue.get('title', '')[:60]}")
    cx_result = assess_complexity(issue)

    cx_score = 5
    if cx_result:
        cx_score = cx_result.get("complexity", 5)
        if not cx_result.get("should_attempt", True):
            reason = cx_result.get("skip_reason", f"Complexity {cx_score}/10")
            logger.info(f"Skipping (complexity {cx_score}/10): {reason}")
            trace.record("complexity_gate", "skip", cx=cx_score)
            return {"skipped": True, "reason": reason, "complexity": cx_score, "trace": trace.to_list()}
    trace.record("complexity_gate", "pass", cx=cx_score)

    # ── Step 2: Static code complexity (radon) ────────────────────────────────
    radon_report = analyze_code_complexity(files, language)
    if radon_report.get("warning"):
        logger.info(f"[radon] {radon_report['warning'].splitlines()[0]}")

    # ── Step 3: Structured signal extraction ──────────────────────────────────
    signals = _safe(
        extract_issue_signals,
        issue.get("title", ""), issue.get("body", "") or "",
        label="signals", default={},
    ) or {}
    signals_block = _safe(format_signals_for_prompt, signals, label="signals_fmt", default="") or ""
    if signals.get("stack_frames") or signals.get("file_refs"):
        logger.info(
            f"[signals] frames={len(signals.get('stack_frames', []))} "
            f"file_refs={len(signals.get('file_refs', []))}"
        )

    # ── Step 4: Repo convention detection ─────────────────────────────────────
    conventions = _safe(detect_repo_conventions, files, language, label="conventions", default={}) or {}
    conventions_block = _safe(format_conventions_for_prompt, conventions, label="conv_fmt", default="") or ""

    # ── Step 5: Context compression (signal-augmented keywords) ───────────────
    issue_text = issue.get("title", "") + " " + (issue.get("body", "") or "")
    keywords = _extract_keywords(issue_text)
    for ident in signals.get("identifiers", []):
        if ident not in keywords:
            keywords.append(ident)
    code_context = compress_context(files, keywords)
    logger.info(f"Context compressed: {len(files)} files → {len(code_context)} chars")

    if dry_run:
        logger.info(
            f"[DRY RUN] Would run fix pipeline | language={language} "
            f"keywords={keywords[:5]} context_chars={len(code_context)}"
        )
        return {
            "dry_run": True,
            "would_attempt": True,
            "language": language,
            "context_chars": len(code_context),
            "keywords": keywords[:10],
            "radon_max_complexity": radon_report.get("max_complexity", 0),
            "signals": {k: len(v) for k, v in signals.items()} if signals else {},
            "conventions": conventions.get("rules", []),
        }

    # ── Step 6+7: RAG — past wins and past failures ───────────────────────────
    rag_wins = _safe(retrieve_similar_past_repairs, issue, limit=2, label="rag_wins", default=[]) or []
    rag_losses = _safe(retrieve_similar_past_failures, issue, limit=1, label="rag_losses", default=[]) or []
    wins_block = _safe(format_past_repairs_for_prompt, rag_wins, label="wins_fmt", default="") or ""
    losses_block = _safe(format_past_failures_for_prompt, rag_losses, label="losses_fmt", default="") or ""
    if rag_wins:
        logger.info(f"[rag] injected {len(rag_wins)} past success(es)")
    if rag_losses:
        logger.info(f"[rag] injected {len(rag_losses)} past failure(s)")

    # ── Step 8: GitHub repo-specific examples ─────────────────────────────────
    repo_examples_block = ""
    try:
        from github_examples import find_similar_resolved_issues, format_examples_for_prompt
        repo_examples = find_similar_resolved_issues(
            issue.get("issue_url", ""),
            issue.get("title", ""),
            issue.get("body", "") or "",
        )
        repo_examples_block = format_examples_for_prompt(repo_examples)
    except Exception as exc:
        logger.warning(f"[github_examples] non-fatal: {exc}")

    # ── Step 9: Adaptive few-shot from top RAG win ────────────────────────────
    adaptive_few_shot = _safe(build_adaptive_few_shot, rag_wins, label="few_shot", default="") or ""

    # ── Step 9b: Pre-symbol resolution from issue text ────────────────────────
    issue_symbol_text = (
        (issue.get("title", "") or "") + "\n"
        + (issue.get("body", "") or "")[:1500] + "\n"
        + " ".join(signals.get("identifiers", []))
    )
    pre_defs = _safe(
        resolve_symbols, issue_symbol_text, files, language,
        label="pre_symbols", default=[],
    ) or []
    pre_symbols_block = _safe(format_symbols_for_prompt, pre_defs, label="pre_symbols_fmt", default="") or ""
    if pre_defs:
        logger.info(f"[symbols] pre-resolved {len(pre_defs)} definition(s) from issue text")
        trace.record("symbols_pre", "resolved", count=len(pre_defs))

    extra_context_parts = [
        b for b in (
            radon_report.get("warning", ""),
            wins_block,
            losses_block,
            repo_examples_block,
            pre_symbols_block,
        ) if b
    ]
    extra_context = "\n\n".join(extra_context_parts)

    # ── Step 10: Primary two-pass fix ─────────────────────────────────────────
    active_model = get_routed_model("fix")
    logger.info(f"Primary fix attempt — model={active_model}")
    result = _generate_fix_candidate(
        issue, code_context, language, extra_context, "", active_model,
        signals_block=signals_block,
        conventions_block=conventions_block,
        adaptive_few_shot=adaptive_few_shot,
    )
    primary_confidence = result.get("confidence", 0) if result else 0
    trace.record("primary_fix", "produced" if result else "none", confidence=primary_confidence)

    # ── Step 11: Tree-of-Thought for mid-complexity uncertain fixes ───────────
    if 3 <= cx_score <= 5 and primary_confidence < 8 and result is not None:
        logger.info(f"[tot] cx={cx_score} conf={primary_confidence} — running ToT (3 framings)")
        candidates = [result]
        for framing in TOT_FRAMINGS:
            cand = _generate_fix_candidate(
                issue, code_context, language, extra_context, framing, active_model,
                signals_block=signals_block,
                conventions_block=conventions_block,
                adaptive_few_shot=adaptive_few_shot,
            )
            if cand:
                candidates.append(cand)
        candidates.sort(key=lambda c: c.get("confidence", 0), reverse=True)
        best = candidates[0]
        if best.get("confidence", 0) > primary_confidence:
            logger.info(
                f"[tot] best framing improved confidence "
                f"{primary_confidence} → {best.get('confidence')}"
            )
            result = best
            primary_confidence = best.get("confidence", 0)

    # ── Step 12: Simplification retry if still low confidence ─────────────────
    if primary_confidence < 7:
        logger.info(f"Confidence {primary_confidence}/10 — retrying with simplified prompt…")
        retry_reason = (
            f"Confidence was {primary_confidence}/10"
            if result else "Primary attempt returned no result"
        )
        failed_str = json.dumps(result, indent=2)[:400] if result else "No result"
        retry_prompt = build_retry_prompt(issue, failed_str, retry_reason)
        try:
            raw2 = call_ollama_with_retry(retry_prompt)
            retry_result = parse_and_validate_response(raw2)
            if retry_result and retry_result.get("confidence", 0) > primary_confidence:
                logger.info(
                    f"Retry improved confidence: "
                    f"{primary_confidence} → {retry_result['confidence']}"
                )
                result = retry_result
        except Exception as e:
            logger.error(f"Retry prompt failed: {e}")

    if result is None:
        logger.error("All fix attempts returned no parseable result")
        _safe(remember_failed_repair, issue, None, "no parseable result", label="failure_persist")
        return None

    # ── Step 12b: Post-fix local analyses ─────────────────────────────────────
    # Caller impact: how many other files use the symbols we're touching?
    caller_report = _safe(analyze_caller_impact, result, files, label="caller_impact", default={}) or {}
    if caller_report.get("callers"):
        logger.info(
            f"[caller_impact] blast_radius={caller_report.get('blast_radius', 0)} "
            f"across {len(caller_report['callers'])} other file(s)"
        )
        result["caller_impact"] = caller_report
        trace.record("caller_impact", "computed",
                     blast_radius=caller_report.get("blast_radius", 0),
                     callers=len(caller_report.get("callers", [])))
        # Heavy blast radius → small confidence penalty
        if caller_report.get("blast_radius", 0) >= 10 and result.get("confidence", 0) > 6:
            result["confidence"] = max(6, result["confidence"] - 1)

    # Code-grounding: did the model quote real code or invent it?
    grounding = _safe(verify_code_grounding, result, files, label="grounding", default={}) or {}
    if grounding.get("total", 0) > 0:
        result["code_grounding"] = grounding
        ratio = grounding.get("ratio", 1.0)
        logger.info(
            f"[grounding] {grounding['grounded']}/{grounding['total']} quoted snippets verified"
        )
        trace.record("grounding", f"{int(ratio * 100)}%", missing=len(grounding.get("quotes_missing", [])))
        if ratio < 0.5 and result.get("confidence", 0) > 5:
            logger.warning("[grounding] majority of quoted code is NOT in the loaded files — penalising confidence")
            result["confidence"] = max(4, result["confidence"] - 2)

    # Test coverage of modified files
    cov_report = _safe(detect_test_coverage, files, result, language, label="test_coverage", default={}) or {}
    if cov_report.get("modified_files_uncovered"):
        result["test_coverage"] = cov_report
        logger.info(
            f"[test_coverage] {len(cov_report['modified_files_uncovered'])} modified file(s) "
            f"lack matching tests in loaded context"
        )
        trace.record("test_coverage", "missing",
                     uncovered=len(cov_report.get("modified_files_uncovered", [])))

    # Diff minimality
    minimality = _safe(score_diff_minimality, result, label="minimality", default={}) or {}
    if minimality:
        result["minimality"] = minimality
        score = minimality.get("minimality_score", 10)
        trace.record("minimality", str(score),
                     files_touched=minimality.get("files_touched", 0),
                     lines=minimality.get("lines_added", 0) + minimality.get("lines_removed", 0))
        if score <= 4 and result.get("confidence", 0) > 6:
            logger.info(f"[minimality] score={score}/10 — diff is bloated; penalising confidence")
            result["confidence"] = max(5, result["confidence"] - 1)

    # Symbol resolution from the produced diagnosis (helps verifier)
    diag_text = (
        (result.get("diagnosis") or "") + "\n"
        + str((result.get("chain_of_thought") or {}).get("root_cause", ""))
    )
    post_defs = _safe(resolve_symbols, diag_text, files, language, label="post_symbols", default=[]) or []
    if post_defs:
        result["resolved_symbols"] = post_defs[:6]
        trace.record("symbols_post", "resolved", count=len(post_defs))

    # ── Step 13: Adversarial local self-verification ──────────────────────────
    confidence = result.get("confidence", 0)
    if confidence >= 6:
        logger.info(f"Local verification (confidence={confidence})…")
        v_prompt = build_verification_prompt(issue, result)
        try:
            raw_v = call_ollama_with_retry(v_prompt)
            v = parse_and_validate_response(raw_v)
            if v:
                v_conf = v.get("confidence", confidence)
                if v.get("verdict") == "revise":
                    logger.info(f"Local verification: revise — {v.get('revision_notes', '')}")
                    result["verification_verdict"] = "revise"
                    result["revision_notes"] = v.get("revision_notes", "")
                    result["confidence"] = min(confidence, v_conf)
                else:
                    result["verified"] = True
                    result["confidence"] = v_conf
                    logger.info(f"Local verification approved (confidence={v_conf})")
        except Exception as e:
            logger.warning(f"Local verification non-fatal error: {e}")

    # ── Step 14: Chain-of-Verification ────────────────────────────────────────
    confidence = result.get("confidence", 0)
    if confidence >= 6:
        try:
            cove = chain_of_verification(
                issue, result,
                call_ollama_extract, parse_and_validate_response,
                model=active_model, role_primer=ROLE_PRIMER,
            )
            result["cove"] = cove
            logger.info(
                f"[cove] verdict={cove['verdict']} answered={cove['answers_count']} "
                f"concerns={len(cove['concerns'])}"
            )
            if cove["verdict"] == "revise":
                result["verification_verdict"] = "revise"
                merged = (result.get("revision_notes", "") + " | cove: " + cove["revision_notes"]).strip(" |")
                result["revision_notes"] = merged
                # Each high-concern question drops confidence by 1, floor 3
                result["confidence"] = max(3, confidence - max(1, len(cove["concerns"])))
        except Exception as exc:
            logger.warning(f"[cove] non-fatal: {exc}")

    # ── Step 15: Iterative refinement on 'revise' ─────────────────────────────
    if result.get("verification_verdict") == "revise":
        critique = result.get("revision_notes", "")
        if critique:
            logger.info(f"[refine] feeding critique back into a fresh attempt")
            try:
                refinement_context = (
                    extra_context
                    + "\n\n## Prior attempt critique (you MUST address every point below)\n"
                    + critique[:2000]
                    + "\n\n## Your previous (rejected) fix\n"
                    + json.dumps(result.get("fix", {}), indent=2)[:1500]
                )
                refined = _generate_fix_candidate(
                    issue, code_context, language, refinement_context,
                    "BIAS: Address EVERY critique point above. Be more careful than the prior attempt.",
                    active_model,
                    signals_block=signals_block,
                    conventions_block=conventions_block,
                    adaptive_few_shot=adaptive_few_shot,
                )
                if refined and refined.get("confidence", 0) >= result.get("confidence", 0):
                    logger.info(
                        f"[refine] refined fix accepted: conf "
                        f"{result.get('confidence', 0)} → {refined.get('confidence', 0)}"
                    )
                    refined["refined_from"] = "previous attempt + critique"
                    result = refined
            except Exception as exc:
                logger.warning(f"[refine] non-fatal: {exc}")

    # ── Step 16: Pre-flight patch validation + auto-correction ────────────────
    try:
        is_valid, val_errors = validate_proposed_patch(result, files, language)
        if not is_valid:
            logger.warning(f"[patch_validate] failed: {val_errors[:3]}")
            # One auto-correction attempt with the actual file contents shown
            try:
                repair_prompt = build_patch_repair_prompt(issue, result, val_errors, files)
                raw_repair = call_ollama_extract(
                    repair_prompt, system=ROLE_PRIMER, model=active_model
                )
                repaired = parse_and_validate_response(raw_repair)
                if repaired:
                    is_valid2, val_errors2 = validate_proposed_patch(repaired, files, language)
                    if is_valid2:
                        logger.info("[patch_validate] auto-correction succeeded")
                        # Preserve original metadata
                        for k in ("verified", "verification_verdict", "cove", "groq_verdict"):
                            if k in result and k not in repaired:
                                repaired[k] = result[k]
                        repaired["confidence"] = max(
                            repaired.get("confidence", 0), result.get("confidence", 0) - 1
                        )
                        repaired["patch_auto_corrected"] = True
                        result = repaired
                    else:
                        logger.warning(f"[patch_validate] still invalid after correction: {val_errors2[:3]}")
                        result["confidence"] = min(result.get("confidence", 0), 5)
                        result["patch_validation_errors"] = val_errors2
                else:
                    result["confidence"] = min(result.get("confidence", 0), 5)
                    result["patch_validation_errors"] = val_errors
            except Exception as exc:
                logger.warning(f"[patch_validate] auto-correction failed: {exc}")
                result["confidence"] = min(result.get("confidence", 0), 5)
                result["patch_validation_errors"] = val_errors
        else:
            result["patch_preflight_passed"] = True
    except Exception as exc:
        logger.warning(f"[patch_validate] skipped: {exc}")

    # ── Step 17: Cross-model verification via Groq (optional) ─────────────────
    confidence = result.get("confidence", 0)
    if confidence >= 6:
        try:
            from groq_verifier import cross_verify_fix, is_available
            if is_available():
                logger.info("[groq] running cross-model verification…")
                groq_verdict = cross_verify_fix(issue, result)
                if groq_verdict:
                    result["groq_verdict"] = groq_verdict
                    g_conf = int(groq_verdict.get("confidence", confidence))
                    g_vote = groq_verdict.get("verdict", "approve")
                    if g_vote in ("revise", "reject"):
                        result["verification_verdict"] = "revise"
                        result["revision_notes"] = (
                            result.get("revision_notes", "") + " | groq: "
                            + (groq_verdict.get("revision_notes") or "")
                        ).strip(" |")
                        result["confidence"] = min(confidence, g_conf)
                        logger.info(
                            f"[groq] disagrees ({g_vote}, conf={g_conf}) → "
                            f"final conf={result['confidence']}"
                        )
                    else:
                        boosted = min(10, int(round((confidence + g_conf) / 2)) + 1)
                        result["confidence"] = max(confidence, boosted)
                        logger.info(
                            f"[groq] approves (conf={g_conf}) → "
                            f"final conf={result['confidence']}"
                        )
        except Exception as exc:
            logger.warning(f"[groq] non-fatal: {exc}")

    # ── Step 18: Persist outcome to RAG (win or loss) + attach trace ──────────
    final_conf = result.get("confidence", 0)
    trace.record("final", "win" if final_conf >= 8 else ("loss" if final_conf < 5 else "borderline"),
                 confidence=final_conf,
                 verdict=result.get("verification_verdict", "approve"))
    result["trace"] = trace.to_list()
    result["intent"] = intent_info
    logger.info(f"[trace] {trace.summary()}")

    if final_conf >= 8 and not result.get("patch_validation_errors"):
        remember_successful_repair(issue, result, final_conf)
    elif final_conf < 5 or result.get("patch_validation_errors"):
        failure_reason = "low confidence"
        if result.get("patch_validation_errors"):
            failure_reason = f"patch validation: {result['patch_validation_errors'][:2]}"
        elif result.get("verification_verdict") == "revise":
            failure_reason = f"verification revised: {result.get('revision_notes', '')[:200]}"
        _safe(remember_failed_repair, issue, result, failure_reason, label="failure_persist")

    return result
