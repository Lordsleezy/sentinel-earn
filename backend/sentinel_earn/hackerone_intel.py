"""
HackerOne program intelligence — parse bounty-targets-data + program metadata.

Produces structured ProgramIntel for program-specific Earn reports.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DATASET_URL = (
    "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/hackerone_data.json"
)
_DATASET_CACHE: Optional[List[Dict[str, Any]]] = None
_DATASET_CACHE_AT: Optional[float] = None
_CACHE_TTL = 3600

_SEVERITY_BOUNTY_HINT = {
    "critical": ("$10,000+", 10000),
    "high": ("$2,500", 2500),
    "medium": ("$500", 500),
    "low": ("$100", 100),
    "none": ("$0", 0),
}

_MOBILE_TYPES = {"GOOGLE_PLAY_APP_ID", "APPLE_STORE_APP_ID", "ANDROID", "IOS", "MOBILE"}
_AUTH_KEYWORDS = re.compile(
    r"\b(oauth|sso|saml|login|sign[- ]?in|session|jwt|bearer|auth|2fa|mfa|credential)\b",
    re.I,
)


@dataclass
class ScopeAsset:
    identifier: str
    asset_type: str
    eligible_for_bounty: bool
    eligible_for_submission: bool
    max_severity: str
    instruction: str = ""
    category: str = ""  # mobile | api | commerce | ads | upload | auth | web

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identifier": self.identifier,
            "asset_type": self.asset_type,
            "eligible_for_bounty": self.eligible_for_bounty,
            "max_severity": self.max_severity,
            "instruction": self.instruction,
            "category": self.category,
        }


@dataclass
class ProgramIntel:
    handle: str
    name: str
    url: str
    website: str = ""
    offers_bounties: bool = True
    submission_state: str = "open"
    managed_program: bool = False
    bounty_range_display: str = "Varies"
    bounty_by_severity: Dict[str, str] = field(default_factory=dict)
    avg_days_to_bounty: Optional[float] = None
    in_scope: List[ScopeAsset] = field(default_factory=list)
    out_of_scope: List[ScopeAsset] = field(default_factory=list)
    exclusions: List[str] = field(default_factory=list)
    authentication_notes: List[str] = field(default_factory=list)
    mobile_targets: List[ScopeAsset] = field(default_factory=list)
    web_targets: List[ScopeAsset] = field(default_factory=list)
    source: str = "hackerone"
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat())
    parse_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handle": self.handle,
            "name": self.name,
            "url": self.url,
            "website": self.website,
            "offers_bounties": self.offers_bounties,
            "submission_state": self.submission_state,
            "bounty_range_display": self.bounty_range_display,
            "bounty_by_severity": self.bounty_by_severity,
            "in_scope": [a.to_dict() for a in self.in_scope],
            "out_of_scope": [a.to_dict() for a in self.out_of_scope],
            "exclusions": self.exclusions,
            "authentication_notes": self.authentication_notes,
            "mobile_count": len(self.mobile_targets),
            "web_count": len(self.web_targets),
            "fetched_at": self.fetched_at,
            "parse_warnings": self.parse_warnings,
        }


def _fetch_dataset(force: bool = False) -> List[Dict[str, Any]]:
    """Load raw HackerOne JSON via shared Earn discovery (disk cache + upstream)."""
    global _DATASET_CACHE, _DATASET_CACHE_AT
    import time
    now = time.time()
    if not force and _DATASET_CACHE and _DATASET_CACHE_AT and (now - _DATASET_CACHE_AT) < _CACHE_TTL:
        return _DATASET_CACHE
    try:
        from sentinel_earn.hackerone_discovery import UPSTREAM_URL, _fetch_upstream
        raw, err = _fetch_upstream()
        if err and _DATASET_CACHE:
            return _DATASET_CACHE
        if err:
            logger.warning("[EARN] dataset load failed: %s", err)
            return []
        if not isinstance(raw, list):
            return _DATASET_CACHE or []
        _DATASET_CACHE = raw
        _DATASET_CACHE_AT = now
        return raw
    except Exception as e:
        logger.warning("[EARN] bounty-targets-data fetch failed: %s", e)
        return _DATASET_CACHE or []


def _handle_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"hackerone\.com/([^/?#]+)", url, re.I)
    if m:
        h = m.group(1).strip()
        if h not in ("programs", "hacktivity", "directory", "teams"):
            return h
    return None


def _classify_url_asset(identifier: str) -> str:
    low = identifier.lower()
    if re.search(r"\b(api|graphql|gql|-api\.|/api)\b", low) or ".api." in low or low.startswith("api."):
        return "api"
    if any(k in low for k in ("checkout", "cart", "cash", "payment", "fintech", "wallet", "billing")):
        return "commerce"
    if any(k in low for k in ("ads", "partner", "affiliate", "tracking")):
        return "ads_partners"
    if any(k in low for k in ("upload", "fileupload", "media", "video", "attachment")):
        return "file_upload"
    if any(k in low for k in ("auth", "login", "sso", "oauth", "identity", "account", "member")):
        return "authentication"
    if any(k in low for k in ("cms", "admin", "internal", "corp", "developer")):
        return "cms_admin"
    if any(k in low for k in ("feed", "notification", "push", "message")):
        return "messaging"
    return "web_general"


def _asset_from_raw(raw: Dict[str, Any]) -> ScopeAsset:
    ident = (raw.get("asset_identifier") or "").strip()
    atype = (raw.get("asset_type") or "URL").strip()
    instr = (raw.get("instruction") or "").strip()
    if atype in _MOBILE_TYPES:
        cat = "mobile"
    elif atype == "URL" or "." in ident:
        cat = _classify_url_asset(ident)
    else:
        cat = "other"
    return ScopeAsset(
        identifier=ident,
        asset_type=atype,
        eligible_for_bounty=bool(raw.get("eligible_for_bounty")),
        eligible_for_submission=bool(raw.get("eligible_for_submission", True)),
        max_severity=(raw.get("max_severity") or "medium").lower(),
        instruction=instr,
        category=cat,
    )


def _derive_bounty_tables(program: Dict[str, Any], in_scope: List[ScopeAsset]) -> Tuple[str, Dict[str, str]]:
    attrs = program.get("attributes") or {}
    min_b = attrs.get("minimum_bounty_table") or {}
    max_b = attrs.get("maximum_bounty_table") or {}
    by_sev: Dict[str, str] = {}

    for sev in ("critical", "high", "medium", "low"):
        mn = min_b.get(sev)
        mx = max_b.get(sev)
        if mn is not None or mx is not None:
            try:
                mn_i = int(float(mn)) if mn else 0
                mx_i = int(float(mx)) if mx else 0
                if mn_i and mx_i:
                    by_sev[sev] = f"${mn_i:,} – ${mx_i:,}"
                elif mx_i:
                    by_sev[sev] = f"Up to ${mx_i:,}"
                elif mn_i:
                    by_sev[sev] = f"From ${mn_i:,}"
            except (TypeError, ValueError):
                pass
        if sev not in by_sev:
            hint, _ = _SEVERITY_BOUNTY_HINT.get(sev, ("Varies", 0))
            by_sev[sev] = hint

    # Highest severity among bounty-eligible assets
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
    sevs = [a.max_severity for a in in_scope if a.eligible_for_bounty and a.max_severity]
    best = min(sevs, key=lambda s: order.get(s, 9)) if sevs else None
    display = by_sev.get(best, "Varies") if best else "Varies"
    if program.get("offers_bounties") and attrs.get("bounty_amount"):
        try:
            display = f"${int(float(attrs['bounty_amount'])):,}"
        except (TypeError, ValueError):
            display = str(attrs["bounty_amount"])
    return display, by_sev


def _infer_exclusions(program: Dict[str, Any], out_scope: List[ScopeAsset]) -> List[str]:
    exclusions: List[str] = []
    if not program.get("offers_bounties"):
        exclusions.append("Vulnerability Disclosure Program only — no monetary bounties")
    if program.get("submission_state") == "closed":
        exclusions.append("Program submission state: closed")
    if program.get("managed_program"):
        exclusions.append("Managed program — triage may impose additional rules")
    if len(out_scope) > 0:
        exclusions.append(f"{len(out_scope)} assets explicitly marked out of scope (see list below)")
    exclusions.append("Social engineering, physical attacks, and denial-of-service unless explicitly in scope")
    exclusions.append("Findings on out-of-scope assets are typically closed as Not Applicable")
    return exclusions


def _infer_authentication(in_scope: List[ScopeAsset]) -> List[str]:
    notes: List[str] = []
    auth_assets = [a for a in in_scope if a.category == "authentication"]
    for a in auth_assets[:8]:
        notes.append(f"Auth-related in-scope asset: {a.identifier}")
    for a in in_scope:
        if a.instruction and _AUTH_KEYWORDS.search(a.instruction):
            snippet = a.instruction[:120].replace("\n", " ")
            notes.append(f"{a.identifier}: instruction mentions authentication ({snippet}…)")
    mobile = [a for a in in_scope if a.category == "mobile"]
    if mobile:
        notes.append(
            f"{len(mobile)} mobile app target(s) — test authenticated flows, token storage, "
            "and API calls from the mobile client"
        )
    api_hosts = [a for a in in_scope if a.category == "api"]
    if api_hosts:
        notes.append(
            f"{len(api_hosts)} API host(s) — verify session/OAuth tokens on mobile and web API routes"
        )
    if not notes:
        notes.append("No explicit auth assets listed — use program-issued test accounts if required by policy")
    return notes[:12]


def parse_hackerone_record(program: Dict[str, Any]) -> ProgramIntel:
    """Parse full HackerOne program record from bounty-targets-data."""
    handle = (
        program.get("handle")
        or program.get("program")
        or program.get("name")
        or "unknown"
    )
    targets = program.get("targets") or {}
    in_raw = targets.get("in_scope") or []
    out_raw = targets.get("out_of_scope") or []

    in_scope = [_asset_from_raw(s) for s in in_raw if s.get("asset_identifier")]
    out_scope = [_asset_from_raw(s) for s in out_raw if s.get("asset_identifier")]
    bounty_display, bounty_by_sev = _derive_bounty_tables(program, in_scope)

    intel = ProgramIntel(
        handle=handle,
        name=program.get("name") or program.get("title") or handle,
        url=program.get("url") or f"https://hackerone.com/{handle}",
        website=program.get("website") or "",
        offers_bounties=bool(program.get("offers_bounties")),
        submission_state=program.get("submission_state") or "unknown",
        managed_program=bool(program.get("managed_program")),
        bounty_range_display=bounty_display,
        bounty_by_severity=bounty_by_sev,
        avg_days_to_bounty=round(program.get("average_time_to_bounty_awarded"), 1)
        if program.get("average_time_to_bounty_awarded") else None,
        in_scope=in_scope,
        out_of_scope=out_scope,
        exclusions=_infer_exclusions(program, out_scope),
        authentication_notes=_infer_authentication(in_scope),
        mobile_targets=[a for a in in_scope if a.category == "mobile"],
        web_targets=[a for a in in_scope if a.category != "mobile"],
    )
    return intel


def resolve_program_intel(
    title: str = "",
    url: str = "",
    scope_hints: Optional[List[str]] = None,
    program_data: Optional[Dict[str, Any]] = None,
) -> ProgramIntel:
    """
    Resolve structured program intel by handle/URL, embedded scan payload, or scope hints.
    """
    warnings: List[str] = []

    if program_data and program_data.get("targets"):
        intel = parse_hackerone_record(program_data)
        intel.parse_warnings = warnings
        return intel

    handle = _handle_from_url(url) or ""
    if not handle and program_data:
        handle = program_data.get("handle") or program_data.get("program") or ""

    dataset = _fetch_dataset()
    record: Optional[Dict[str, Any]] = None
    if handle:
        for p in dataset:
            if (p.get("handle") or "").lower() == handle.lower():
                record = p
                break
    if not record and title:
        tlow = title.lower()
        for p in dataset:
            if (p.get("name") or "").lower() == tlow or (p.get("handle") or "").lower() == tlow:
                record = p
                break

    if record:
        intel = parse_hackerone_record(record)
    else:
        warnings.append("Program not found in bounty-targets-data — using scope hints only")
        in_scope = []
        for s in scope_hints or []:
            if not s:
                continue
            raw = {"asset_identifier": s, "asset_type": "URL", "eligible_for_bounty": True,
                   "max_severity": "medium"}
            in_scope.append(_asset_from_raw(raw))
        intel = ProgramIntel(
            handle=handle or re.sub(r"[^\w\-]", "_", title.lower())[:40],
            name=title or "Unknown Program",
            url=url or "",
            in_scope=in_scope,
            web_targets=in_scope,
            bounty_range_display=program_data.get("reward", "Varies") if program_data else "Varies",
        )

    if program_data:
        if program_data.get("reward") and intel.bounty_range_display == "Varies":
            intel.bounty_range_display = program_data.get("reward", intel.bounty_range_display)
        if program_data.get("url") and not intel.url:
            intel.url = program_data["url"]

    intel.parse_warnings = warnings
    return intel


def fetch_program_page_notes(handle: str) -> List[str]:
    """Best-effort fetch of public HackerOne policy page (may be empty without session)."""
    notes: List[str] = []
    if not handle:
        return notes
    try:
        import httpx
        for path in (f"https://hackerone.com/{handle}", f"https://hackerone.com/{handle}.json"):
            try:
                r = httpx.get(path, timeout=12, headers={"User-Agent": "SentinelEarn/1.0"})
                if r.status_code == 200 and len(r.text) > 200:
                    text = r.text[:8000]
                    if "out of scope" in text.lower():
                        notes.append("Policy page mentions out-of-scope rules (HTML snapshot)")
                    break
            except Exception:
                continue
    except Exception as e:
        notes.append(f"Program page fetch skipped: {e}")
    return notes
