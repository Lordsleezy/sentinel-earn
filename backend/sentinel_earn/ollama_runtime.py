"""Ollama install, start, and model pull — adapted from SentinelAI model_runtime."""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from sentinel_earn.config import get_data_dir, load_settings

logger = logging.getLogger("sentinel_earn.ollama")

OLLAMA_WIN_INSTALLER = "https://ollama.com/download/OllamaSetup.exe"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_dir() -> Path:
    d = get_data_dir() / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ollama_host() -> str:
    return load_settings().get("ollama_host", "http://127.0.0.1:11434").rstrip("/")


def _windows_ollama_exe() -> Optional[Path]:
    if platform.system() != "Windows":
        return None
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
    for name in ("ollama.exe", "Ollama.exe"):
        p = local / name
        if p.is_file():
            return p
    return None


def _ollama_cli() -> Optional[str]:
    exe = shutil.which("ollama")
    if exe:
        return exe
    win = _windows_ollama_exe()
    return str(win) if win else None


def ollama_installed() -> bool:
    return _ollama_cli() is not None


def ollama_running() -> bool:
    try:
        import httpx
        r = httpx.get(f"{_ollama_host()}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def list_installed_models() -> Dict[str, str]:
    try:
        import httpx
        r = httpx.get(f"{_ollama_host()}/api/tags", timeout=3)
        if r.status_code != 200:
            return {}
        out = {}
        for m in r.json().get("models", []):
            name = m.get("name", "")
            if name:
                out[name] = m.get("digest", "")[:12]
        return out
    except Exception:
        return {}


def model_present(tag: str, installed: Optional[Dict[str, str]] = None) -> bool:
    installed = installed or list_installed_models()
    if tag in installed:
        return True
    base = tag.split(":")[0].lower()
    for k in installed:
        kl = k.lower()
        if kl == tag.lower() or kl.startswith(tag.lower() + ":"):
            return True
        if kl.split(":")[0] == base:
            return True
    return False


def ensure_ollama_running() -> Dict[str, Any]:
    if ollama_running():
        return {"ok": True, "action": "already_running"}

    exe = _ollama_cli()
    if exe:
        try:
            if platform.system() == "Windows":
                win_app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "Ollama.exe"
                if win_app.is_file():
                    subprocess.Popen(
                        [str(win_app)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            subprocess.Popen(
                [exe, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            logger.warning("start ollama serve: %s", e)

    deadline = time.time() + 90
    while time.time() < deadline:
        if ollama_running():
            return {"ok": True, "action": "started"}
        time.sleep(2)
    return {"ok": False, "action": "start_timeout", "user_message": "Ollama did not start in time."}


def install_ollama_windows(
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    if platform.system() != "Windows":
        return {
            "ok": False,
            "action": "manual",
            "url": "https://ollama.com/download",
            "user_message": "Install Ollama from ollama.com, then restart Sentinel Earn.",
        }
    if ollama_installed():
        return ensure_ollama_running()

    dest = _runtime_dir() / "OllamaSetup.exe"
    try:
        import httpx
        with httpx.stream("GET", OLLAMA_WIN_INSTALLER, follow_redirects=True, timeout=120) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    done += len(chunk)
                    pct = int(done * 100 / total) if total else min(95, done // 500000)
                    if progress_cb:
                        progress_cb({"download_percent": pct, "message": "Downloading Ollama installer…"})
    except Exception as e:
        logger.exception("ollama download failed")
        return {
            "ok": False,
            "action": "manual",
            "url": "https://ollama.com/download",
            "error": str(e)[:200],
            "user_message": "Could not download Ollama automatically. Install from ollama.com.",
        }

    if progress_cb:
        progress_cb({"download_percent": 100, "message": "Installing Ollama…"})

    for flag in ("/SILENT", "/VERYSILENT", "/S"):
        try:
            proc = subprocess.run([str(dest), flag], timeout=600, capture_output=True)
            if proc.returncode == 0:
                break
        except Exception:
            continue

    local_bin = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
    if local_bin.is_dir():
        os.environ["PATH"] = str(local_bin) + os.pathsep + os.environ.get("PATH", "")

    deadline = time.time() + 120
    while time.time() < deadline:
        if ollama_installed():
            return ensure_ollama_running()
        time.sleep(3)

    return {
        "ok": False,
        "action": "manual",
        "url": "https://ollama.com/download",
        "user_message": "Ollama install did not complete. Download from ollama.com and retry.",
    }


def install_ollama(progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    if ollama_installed():
        return ensure_ollama_running()
    if platform.system() == "Windows":
        return install_ollama_windows(progress_cb)
    return {
        "ok": False,
        "action": "manual",
        "url": "https://ollama.com/download",
        "user_message": "Install Ollama from ollama.com, then restart Sentinel Earn.",
    }


def _parse_pull_percent(line: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*%", line)
    return int(m.group(1)) if m else None


def pull_model(
    model_name: str,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    if not ollama_running():
        start = ensure_ollama_running()
        if not start.get("ok"):
            return {"ok": False, "user_message": start.get("user_message", "Ollama is offline.")}

    cli = _ollama_cli()
    if not cli:
        return {"ok": False, "user_message": "Ollama CLI not found."}

    try:
        proc = subprocess.Popen(
            [cli, "pull", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        last_pct = 0
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            pct = _parse_pull_percent(line) or last_pct
            last_pct = max(last_pct, pct)
            if progress_cb:
                progress_cb({"percent": last_pct, "message": line[:200], "model": model_name})
        proc.wait(timeout=3600)
        ok = proc.returncode == 0
        return {"ok": ok, "model": model_name, "user_message": "Model ready." if ok else "Model download failed."}
    except FileNotFoundError:
        return {"ok": False, "user_message": "Ollama CLI not found."}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "user_message": "Model download failed."}


def save_ready(model: str, profile: Dict[str, Any]) -> None:
    payload = {"ready": True, "model": model, "profile": profile, "completed_at": _utc()}
    (_runtime_dir() / "ready.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_ready() -> Dict[str, Any]:
    path = _runtime_dir() / "ready.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_setup_complete() -> bool:
    ready = load_ready()
    if not ready.get("ready"):
        return False
    model = ready.get("model") or load_settings().get("ollama_model")
    return model_present(str(model))
