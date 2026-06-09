"""HackerOne / bounty-targets-data scanner.

Parsing lives here; discovery/cache in workers.earn.program_discovery.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SOURCE_NAME = "bounty_targets"

_SEVERITY_REWARD_HINT = {
    "critical": "Up to $10,000+",
    "high":     "Up to $2,500",
    "medium":   "Up to $500",
    "low":      "Up to $100",
}


def parse_hackerone_program(program: Dict[str, Any]) -> Dict[str, Any]:
    offers_bounties = program.get("offers_bounties", False)

    targets = program.get("targets") or {}
    in_scope_raw = targets.get("in_scope") or []

    eligible_scopes = [s for s in in_scope_raw if s.get("eligible_for_bounty")]
    scope_identifiers = [s.get("asset_identifier", "") for s in eligible_scopes if s.get("asset_identifier")]

    severities = [s.get("max_severity", "").lower() for s in eligible_scopes if s.get("max_severity")]
    _order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    best_severity = min(severities, key=lambda s: _order.get(s, 99), default=None) if severities else None

    attrs = program.get("attributes") or {}

    min_b = attrs.get("minimum_bounty_table") or {}
    max_b = attrs.get("maximum_bounty_table") or {}
    bounty_amount = attrs.get("bounty_amount")

    if offers_bounties:
        if bounty_amount:
            try:
                reward = f"${int(float(bounty_amount)):,}"
            except (ValueError, TypeError):
                reward = str(bounty_amount)
        elif min_b or max_b:
            min_val = min_b.get("critical") or min_b.get("high") or 0
            max_val = max_b.get("critical") or max_b.get("high") or 0
            try:
                min_val = int(float(min_val)) if min_val else 0
                max_val = int(float(max_val)) if max_val else 0
            except (ValueError, TypeError):
                min_val = max_val = 0
            if min_val and max_val:
                reward = f"${min_val:,} – ${max_val:,}"
            elif max_val:
                reward = f"Up to ${max_val:,}"
            else:
                reward = _SEVERITY_REWARD_HINT.get(best_severity, "Varies") if best_severity else "Varies"
        elif best_severity and best_severity in _SEVERITY_REWARD_HINT:
            reward = _SEVERITY_REWARD_HINT[best_severity]
        else:
            reward = "Varies"
    else:
        reward = "VDP"

    avg_days = program.get("average_time_to_bounty_awarded")
    handle = program.get("handle") or program.get("name", "unknown")

    return {
        "source": "hackerone",
        "title": program.get("name") or handle,
        "program": handle,
        "handle": handle,
        "reward": reward,
        "reward_range": reward,
        "max_bounty": reward,
        "max_severity": best_severity or "varies",
        "offers_bounties": offers_bounties,
        "scope": scope_identifiers[:3],
        "scope_full": scope_identifiers,
        "scope_count": len(scope_identifiers),
        "avg_days_to_bounty": round(avg_days, 0) if avg_days else None,
        "url": program.get("url") or f"https://hackerone.com/{handle}",
        "website": program.get("website") or "",
        "submission_state": program.get("submission_state") or "open",
        "fetched_at": datetime.now().isoformat(),
        "type": "bounty",
        "targets": targets,
        "offers_swag": program.get("offers_swag"),
        "managed_program": program.get("managed_program"),
    }


def scan(
    limit: int = 50,
    force_refresh: bool = False,
    socketio: Any = None,
) -> List[Dict[str, Any]]:
    """Return normalized bounty programs via discovery engine."""
    from workers.earn.program_discovery import discover_programs
    programs, _meta = discover_programs(
        limit=limit,
        force_refresh=force_refresh,
        refresh=force_refresh,
        socketio=socketio,
    )
    return programs
