# Sentinel Earn

Standalone desktop app for automated bounty hunting: scan GitHub issues and HackerOne programs, generate fixes with **Ollama** (`qwen2.5-coder:14b`), validate against repo test suites, and submit PRs for review.

No dependency on SentinelAI.

## Features

- **GitHub bounty scanner** — finds open issues labeled `bounty` or `good-first-issue`
- **HackerOne program discovery** — syncs public program data via bounty-targets-data
- **Ollama patch generator** — structured fix pipeline with test validation
- **Patch queue** — review-ready fixes before submission
- **PR submission** — fork, branch, push, open pull request
- **Earnings tracker** — submission history and confirmed payouts

## Architecture

```
desktop/          Electron UI (Sentinel Prime dark theme, teal accents)
backend/
  app.py          Flask REST API (:5120)
  sentinel_earn/
    github_scanner.py      GitHub issue discovery + scoring
    hackerone_discovery.py HackerOne program cache + fetch
    hackerone_parser.py    Program normalization
    hackerone_intel.py     Deep program intel structs
    prompt_engine.py       Ollama patch generation (qwen2.5-coder)
    patch_engine.py        Deterministic patch application
    test_runner.py         pytest/jest/npm test detection
    executor.py            Full clone → patch → test → PR pipeline
    pipeline.py            Orchestration + background worker
    db.py                  SQLite persistence
    config.py              Settings + data paths
```

## Requirements

- Python 3.10+
- Node.js 18+ (Electron)
- [Ollama](https://ollama.com) with `qwen2.5-coder:14b` pulled
- GitHub personal access token (repo + fork scope) for scanning/submission

## Setup

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python app.py
```

API runs at `http://127.0.0.1:5120`.

### Desktop

```bash
cd desktop
npm install
npm start
```

The Electron shell auto-starts the Python backend.

### Settings

Configure in the **Settings** tab or edit `%APPDATA%\SentinelEarn\data\settings.json`:

| Key | Description |
|-----|-------------|
| `ollama_host` | Default `http://127.0.0.1:11434` |
| `ollama_model` | Default `qwen2.5-coder:14b` |
| `github_token` | GitHub PAT for API + PR submission |
| `github_username` | Your GitHub username (for forks) |
| `hackerone_username` | Optional HackerOne username |
| `hackerone_api_token` | Optional HackerOne API token |

## Tests

```bash
cd backend
pip install -r requirements.txt
pytest tests/ -q
```

## Workflow

1. **Scan GitHub** — discovers scored bounty issues and queues opportunities
2. **Generate Patch** — clones repo, builds context, calls Ollama, applies patch, runs tests
3. **Review** — patch appears in Patch Queue with `ready_to_submit` status
4. **Submit PR** — pushes branch and opens pull request on upstream repo
5. **Track** — submissions and earnings appear in history panels

## License

MIT — Sentinel Prime Inc.
