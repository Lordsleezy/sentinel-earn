#!/usr/bin/env python3
"""Scheduled bounty scan — GitHub + HackerOne, writes JSON to scan-log/."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sentinel_earn.pipeline import run_full_cycle  # noqa: E402


def main() -> int:
    log_dir = ROOT / "scan-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    result = run_full_cycle()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    payload = {"timestamp": ts, **result}
    text = json.dumps(payload, indent=2)
    (log_dir / "latest.json").write_text(text, encoding="utf-8")
    (log_dir / f"scan-{ts}.json").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
