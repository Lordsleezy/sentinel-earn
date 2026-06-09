"""Sentinel Earn configuration — standalone, no SentinelAI dependencies."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:3b"
APP_NAME = "Sentinel Earn"


def get_data_dir() -> Path:
    override = os.getenv("SENTINEL_EARN_DATA_DIR", "").strip()
    if override:
        p = Path(override)
    else:
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            p = Path(appdata) / "SentinelEarn" / "data"
        else:
            p = Path.home() / ".sentinel-earn" / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_config_path() -> Path:
    return get_data_dir() / "settings.json"


def get_cache_dir() -> Path:
    d = get_data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_workspace_dir() -> Path:
    d = Path(__file__).resolve().parent.parent / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_settings() -> Dict[str, Any]:
    path = get_config_path()
    defaults = {
        "ollama_host": DEFAULT_OLLAMA_HOST,
        "ollama_model": DEFAULT_OLLAMA_MODEL,
        "github_token": "",
        "github_username": "",
        "hackerone_username": "",
        "hackerone_api_token": "",
        "scan_interval_minutes": 60,
        "auto_generate_patches": False,
    }
    if not path.exists():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {**defaults, **data}
    except Exception:
        pass
    return defaults


def save_settings(settings: Dict[str, Any]) -> None:
    path = get_config_path()
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    _sync_env(settings)


def _sync_env(settings: Dict[str, Any]) -> None:
    """Mirror settings into os.environ for modules that read env vars."""
    mapping = {
        "ollama_host": "OLLAMA_HOST",
        "ollama_model": "OLLAMA_MODEL",
        "github_token": "GITHUB_TOKEN",
        "github_username": "GITHUB_USERNAME",
        "hackerone_username": "HACKERONE_USERNAME",
        "hackerone_api_token": "HACKERONE_API_TOKEN",
    }
    for key, env_key in mapping.items():
        val = settings.get(key, "")
        if val:
            os.environ[env_key] = str(val)


def apply_settings() -> Dict[str, Any]:
    settings = load_settings()
    _sync_env(settings)
    return settings
