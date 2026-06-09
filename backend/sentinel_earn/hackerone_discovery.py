"""
Earn program discovery — bounty-targets-data upstream with disk cache.

Data origin (not HackerOne authenticated API):
  https://github.com/arkadiyt/bounty-targets-data (synced HackerOne program JSON)

No hardcoded program lists. On fetch failure, only aged disk cache may be used.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sentinel_earn.hackerone_parser import parse_hackerone_program

logger = logging.getLogger(__name__)


def earn_log(socketio: Any, message: str, level: str = "info") -> None:
    if level == "error":
        logger.error("[EARN] %s", message)
    elif level == "warning":
        logger.warning("[EARN] %s", message)
    else:
        logger.info("[EARN] %s", message)


UPSTREAM_NAME = "bounty-targets-data"
UPSTREAM_URL = (
    "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/hackerone_data.json"
)
CACHE_MAX_AGE_SEC = 24 * 3600  # auto-refresh threshold

from sentinel_earn.config import get_cache_dir

_VAULT_ROOT = get_cache_dir()
_PROGRAMS_PATH = _VAULT_ROOT / "hackerone_programs.json"
_META_PATH = _VAULT_ROOT / "discovery_meta.json"


@dataclass
class DiscoveryMeta:
    source: str = "none"  # api | cache | cache_stale_fallback
    upstream: str = UPSTREAM_NAME
    program_count: int = 0
    discovered_total: int = 0
    bounty_eligible_total: int = 0
    cache_age_minutes: Optional[float] = None
    last_refresh: Optional[str] = None
    api_status: str = "unknown"  # ok | error | skipped
    duration_ms: float = 0.0
    force_refresh: bool = False
    error: Optional[str] = None
    logs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _cache_paths() -> Tuple[Path, Path]:
    _VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    return _PROGRAMS_PATH, _META_PATH


def _read_meta() -> Dict[str, Any]:
    _, meta_path = _cache_paths()
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(programs: List[Dict[str, Any]], meta: DiscoveryMeta) -> None:
    prog_path, meta_path = _cache_paths()
    now = datetime.now(timezone.utc).isoformat()
    for p in programs:
        p["cached_at"] = now
        p["discovery_source"] = meta.source
    prog_path.write_text(json.dumps(programs, indent=2), encoding="utf-8")
    meta.last_refresh = now
    meta_path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")


def _load_cache() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    prog_path, _ = _cache_paths()
    meta = _read_meta()
    if not prog_path.exists():
        return [], meta
    try:
        data = json.loads(prog_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else [], meta
    except Exception as e:
        logger.warning("[EARN] cache read failed: %s", e)
        return [], meta


def _cache_age_minutes(meta: Dict[str, Any]) -> Optional[float]:
    ts = meta.get("last_refresh") or meta.get("fetched_at")
    if not ts:
        return None
    try:
        # Handle Z suffix
        if ts.endswith("Z"):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        age_sec = (datetime.now(timezone.utc) - dt).total_seconds()
        return round(age_sec / 60.0, 1)
    except Exception:
        return None


def _cache_is_fresh(meta: Dict[str, Any]) -> bool:
    age = _cache_age_minutes(meta)
    if age is None:
        return False
    return age * 60 < CACHE_MAX_AGE_SEC


def _fetch_upstream(socketio: Any = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """HTTP fetch raw HackerOne JSON mirror. Returns (raw_list, error)."""
    try:
        import httpx
    except Exception as e:
        return [], f"httpx unavailable: {e}"

    try:
        earn_log(socketio, f"Fetching upstream: {UPSTREAM_NAME}")
        t0 = time.time()
        response = httpx.get(UPSTREAM_URL, timeout=30, follow_redirects=True)
        elapsed = round((time.time() - t0) * 1000, 1)
        earn_log(socketio, f"Upstream HTTP {response.status_code} ({elapsed} ms)")
        if response.status_code != 200:
            return [], f"HTTP {response.status_code}"
        raw = response.json()
        if not isinstance(raw, list):
            return [], "invalid JSON (expected array)"
        return raw, None
    except Exception as e:
        logger.warning("[EARN] upstream fetch failed: %s", e)
        return [], str(e)


def _normalize_programs(raw: List[Dict[str, Any]], limit: int) -> Tuple[List[Dict[str, Any]], int, int]:
    parsed = [parse_hackerone_program(item) for item in raw if isinstance(item, dict)]
    _sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "varies": 4}
    parsed.sort(key=lambda p: (
        0 if p.get("offers_bounties") else 1,
        _sev_order.get(p.get("max_severity", "varies"), 5),
        (p.get("title") or "").lower(),
    ))
    bounty_total = sum(1 for p in parsed if p.get("offers_bounties"))
    bounty_programs = [p for p in parsed if p.get("offers_bounties")]
    out = bounty_programs[:limit] if bounty_programs else parsed[:limit]
    return out, len(parsed), bounty_total


def discover_programs(
    limit: int = 50,
    force_refresh: bool = False,
    refresh: bool = False,
    socketio: Any = None,
) -> Tuple[List[Dict[str, Any]], DiscoveryMeta]:
    """
    Load bounty programs for Earn UI.

    force_refresh: bypass cache read entirely; always HTTP fetch.
    refresh: SCAN NOW — always fetch and update cache (even if cache is fresh).
    Auto-refresh: if cache older than 24h, fetch before serving (when refresh/force false).
    """
    t0 = time.time()
    meta = DiscoveryMeta(force_refresh=force_refresh)
    earn_log(socketio, "Loading programs")

    cached, cache_meta = _load_cache()
    cache_age = _cache_age_minutes(cache_meta)
    fresh = _cache_is_fresh(cache_meta) and cached

    need_fetch = force_refresh or refresh or not fresh
    if force_refresh:
        earn_log(socketio, "Force refresh — bypassing cache read")
    elif fresh and not need_fetch:
        earn_log(socketio, "Source: Cache")
        earn_log(socketio, f"Programs discovered: {len(cached)}")
        if cache_age is not None:
            earn_log(socketio, f"Cache age: {cache_age} minutes")
        meta.source = "cache"
        meta.api_status = "skipped"
        meta.program_count = min(limit, len(cached))
        meta.discovered_total = cache_meta.get("discovered_total", len(cached))
        meta.bounty_eligible_total = cache_meta.get("bounty_eligible_total", meta.program_count)
        meta.cache_age_minutes = cache_age
        meta.last_refresh = cache_meta.get("last_refresh")
        meta.duration_ms = round((time.time() - t0) * 1000, 1)
        return cached[:limit], meta

    if refresh and not force_refresh:
        earn_log(socketio, "SCAN refresh — fetching upstream")
    elif need_fetch and cache_age is not None and not force_refresh and not refresh:
        earn_log(socketio, f"Cache stale ({cache_age} min > 24h) — refreshing")

    raw, err = _fetch_upstream(socketio)
    if err:
        meta.api_status = "error"
        meta.error = err
        earn_log(socketio, f"Upstream fetch failed: {err}", "error")
        if cached:
            meta.source = "cache_stale_fallback"
            meta.api_status = "error"
            earn_log(socketio, "Source: Cache (upstream failed — no hardcoded fallback)", "warning")
            earn_log(socketio, f"Programs discovered: {len(cached)}")
            if cache_age is not None:
                earn_log(socketio, f"Cache age: {cache_age} minutes")
            meta.program_count = min(limit, len(cached))
            meta.discovered_total = cache_meta.get("discovered_total", len(cached))
            meta.bounty_eligible_total = cache_meta.get("bounty_eligible_total", 0)
            meta.cache_age_minutes = cache_age
            meta.last_refresh = cache_meta.get("last_refresh")
            meta.duration_ms = round((time.time() - t0) * 1000, 1)
            return cached[:limit], meta
        earn_log(socketio, "Programs discovered: 0", "error")
        meta.duration_ms = round((time.time() - t0) * 1000, 1)
        return [], meta

    programs, total, bounty_total = _normalize_programs(raw, limit)
    # Cache full bounty-eligible set (not just UI limit) for accurate refresh
    all_parsed = [parse_hackerone_program(item) for item in raw if isinstance(item, dict)]
    bounty_only = [p for p in all_parsed if p.get("offers_bounties")]
    _sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "varies": 4}
    bounty_only.sort(key=lambda p: (
        _sev_order.get(p.get("max_severity", "varies"), 5),
        (p.get("title") or "").lower(),
    ))

    meta.source = "api"
    meta.api_status = "ok"
    meta.program_count = len(programs)
    meta.discovered_total = total
    meta.bounty_eligible_total = bounty_total
    meta.cache_age_minutes = 0.0
    earn_log(socketio, "Source: API")
    earn_log(socketio, f"Programs discovered: {total} ({bounty_total} bounty-eligible, showing {len(programs)})")
    earn_log(socketio, "Cache age: 0 minutes")

    _write_cache(bounty_only if bounty_only else all_parsed[:500], meta)
    full_meta = meta.to_dict()
    full_meta["discovered_total"] = total
    full_meta["bounty_eligible_total"] = bounty_total
    full_meta["program_count"] = len(bounty_only) if bounty_only else len(all_parsed)
    _META_PATH.write_text(json.dumps(full_meta, indent=2), encoding="utf-8")

    meta.duration_ms = round((time.time() - t0) * 1000, 1)
    earn_log(socketio, f"Discovery complete in {meta.duration_ms} ms", "success")
    return programs, meta


def get_discovery_diagnostics() -> Dict[str, Any]:
    """Snapshot for Earn diagnostics panel."""
    cached, cache_meta = _load_cache()
    age = _cache_age_minutes(cache_meta)
    return {
        "upstream": UPSTREAM_NAME,
        "upstream_url": UPSTREAM_URL,
        "program_source": cache_meta.get("source", "unknown"),
        "last_refresh": cache_meta.get("last_refresh"),
        "cache_age_minutes": age,
        "cache_fresh": _cache_is_fresh(cache_meta) if cached else False,
        "program_count": len(cached),
        "discovered_total": cache_meta.get("discovered_total", len(cached)),
        "bounty_eligible_total": cache_meta.get("bounty_eligible_total"),
        "api_status": cache_meta.get("api_status", "unknown"),
        "last_error": cache_meta.get("error"),
        "last_duration_ms": cache_meta.get("duration_ms"),
        "force_refresh_available": True,
        "cache_path": str(_PROGRAMS_PATH),
        "auto_refresh_hours": CACHE_MAX_AGE_SEC / 3600,
    }
