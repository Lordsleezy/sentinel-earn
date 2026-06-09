# CODEX Progress — Sentinel Earn Extraction

## Status: Initial standalone release

Extracted from `Lordsleezy/SentinelAI` into a fully independent product at `Lordsleezy/sentinel-earn`.

## Source mapping

| SentinelAI origin | sentinel-earn module |
|-------------------|-------------------|
| `revenue/bounty_pipeline.py` | `sentinel_earn/github_scanner.py` |
| `revenue/test_bounty_pipeline.py` | `backend/tests/test_github_scanner.py` |
| `workers/earn/program_discovery.py` | `sentinel_earn/hackerone_discovery.py` |
| `workers/earn/sources/bounty_targets.py` | `sentinel_earn/hackerone_parser.py` |
| `workers/earn/hackerone_intel.py` | `sentinel_earn/hackerone_intel.py` |
| `executor.py` | `sentinel_earn/executor.py` |
| `prompt_engine.py` | `sentinel_earn/prompt_engine.py` |
| `patch_engine.py` | `sentinel_earn/patch_engine.py` |
| `test_runner.py` | `sentinel_earn/test_runner.py` |
| `context_builder.py` | `sentinel_earn/context_builder.py` |
| `git_operations.py` | `sentinel_earn/git_operations.py` |
| `security.py` | `sentinel_earn/security.py` |
| `db.py` + `queue_manager.py` | `sentinel_earn/db.py`, `queue_manager.py` |

## Removed SentinelAI dependencies

- No imports from `core.*`, `workers.*` (except ported earn code), `desktop_app`, Flask monolith
- Data dir: `%APPDATA%\SentinelEarn\data` (not SentinelAI paths)
- Standalone Flask API on port **5120**
- Electron desktop shell with no SentinelAI auth/socket layers

## Architecture decisions

1. **HackerOne data** — public bounty-targets-data JSON mirror (same as SentinelAI Earn); optional API credentials stored for future authenticated endpoints
2. **Ollama** — `prompt_engine.py` calls `localhost:11434` with configurable model
3. **Patch pipeline** — full executor retained: security check → fork → context → Ollama → patch_engine → test_runner → ready_to_submit
4. **Submission** — user-triggered PR via `execute_submit()` (not auto-submit)
5. **Background worker** — processes `repair_execute` queue tasks every 45s

## Next steps (post-v1)

- [ ] HackerOne authenticated API for live scope sync
- [ ] In-app diff viewer for patch queue
- [ ] Packaged installer (electron-builder)
- [ ] Earnings sync from GitHub Sponsors / issue bounty APIs

## Verification checklist

- [x] GitHub scanner tests pass
- [x] HackerOne parser tests pass
- [x] No SentinelAI import paths remain in ported modules
- [x] README + setup documented
- [x] Electron UI: bounties, patches, history, earnings, settings
