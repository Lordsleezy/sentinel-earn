"""Ollama install, start, and model pull — in-app only, no browser."""
from __future__ import annotations

import json
import logging
import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from sentinel_earn.config import get_data_dir, load_settings

logger = logging.getLogger("sentinel_earn.ollama")

OLLAMA_WIN_INSTALLER = "https://ollama.com/download/OllamaSetup.exe"
PULL_TIMEOUT_SEC = 30 * 60  # 30 minutes

# Approximate model sizes for progress when total unknown (GB)
MODEL_SIZE_GB = {
    "qwen2.5-coder:14b": 9.0,
    "qwen2.5-coder:7b": 4.7,
    "qwen2.5-coder:3b": 2.0,
    "qwen2.5-coder:1.5b": 1.0,
}


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


def clear_ready() -> None:
    path = _runtime_dir() / "ready.json"
    if path.is_file():
        path.unlink(missing_ok=True)


def _pull_state_path() -> Path:
    return _runtime_dir() / "pull_state.json"


def load_pull_state() -> Dict[str, Any]:
    path = _pull_state_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_pull_state(model: str, percent: int, **extra: Any) -> None:
    payload = {
        "model": model,
        "percent": percent,
        "updated_at": _utc(),
        **extra,
    }
    _pull_state_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_pull_state() -> None:
    path = _pull_state_path()
    if path.is_file():
        path.unlink(missing_ok=True)


def has_resumable_pull(model: str) -> bool:
    state = load_pull_state()
    if state.get("model") != model:
        return False
    if model_present(model):
        return False
    return int(state.get("percent") or 0) > 0


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
    return {"ok": False, "user_message": "Ollama did not start. Tap Retry to try again."}


def _parse_size_to_mb(text: str) -> Optional[float]:
    m = re.search(r"([\d.]+)\s*(GB|MB|KB)", text, re.I)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "GB":
        return val * 1024
    if unit == "KB":
        return val / 1024
    return val


def _parse_pull_progress(line: str, model_name: str, started_at: float) -> Dict[str, Any]:
    info: Dict[str, Any] = {"message": line[:200], "model": model_name}
    pct_m = re.search(r"(\d+)\s*%", line)
    if pct_m:
        info["percent"] = int(pct_m.group(1))

    # e.g. 1.2 GB/4.7 GB  or  450 MB/1.8 GB
    frac = re.search(
        r"([\d.]+\s*(?:GB|MB|KB))\s*/\s*([\d.]+\s*(?:GB|MB|KB))",
        line,
        re.I,
    )
    if frac:
        done_mb = _parse_size_to_mb(frac.group(1)) or 0
        total_mb = _parse_size_to_mb(frac.group(2)) or 0
        info["downloaded_mb"] = round(done_mb, 1)
        info["total_mb"] = round(total_mb, 1)
        if total_mb > 0 and "percent" not in info:
            info["percent"] = min(99, int(done_mb * 100 / total_mb))

    speed_m = re.search(r"([\d.]+\s*(?:GB|MB|KB))/s", line, re.I)
    eta_m = re.search(r"(\d+)\s*s(?:\s|$)", line)
    if eta_m:
        info["eta_seconds"] = int(eta_m.group(1))
    elif speed_m and info.get("downloaded_mb") and info.get("total_mb"):
        speed_mb = _parse_size_to_mb(speed_m.group(1)) or 0
        if speed_mb > 0:
            remaining = max(0, info["total_mb"] - info["downloaded_mb"])
            info["eta_seconds"] = int(remaining / speed_mb)

    if "percent" not in info:
        elapsed = max(0.1, time.time() - started_at)
        est_gb = MODEL_SIZE_GB.get(model_name, 3.0)
        est_mb = est_gb * 1024
        # Rough fallback from elapsed time when ollama omits percent
        info["total_mb"] = info.get("total_mb") or round(est_mb, 1)

    return info


def install_ollama_windows(
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    if ollama_installed():
        return ensure_ollama_running()

    dest = _runtime_dir() / "OllamaSetup.exe"
    started = time.time()
    try:
        import httpx
        with httpx.stream("GET", OLLAMA_WIN_INSTALLER, follow_redirects=True, timeout=300) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    done += len(chunk)
                    pct = int(done * 100 / total) if total else min(95, done // 500000)
                    elapsed = max(0.1, time.time() - started)
                    speed = done / elapsed
                    eta = int((total - done) / speed) if total and speed > 0 else None
                    if progress_cb:
                        progress_cb({
                            "phase": "installing_ollama",
                            "percent": pct,
                            "downloaded_mb": round(done / (1024 * 1024), 1),
                            "total_mb": round(total / (1024 * 1024), 1) if total else None,
                            "eta_seconds": eta,
                            "message": "Downloading Ollama runtime…",
                        })
    except Exception as e:
        logger.exception("ollama download failed")
        return {
            "ok": False,
            "user_message": f"Could not download Ollama ({str(e)[:80]}). Check your connection and tap Retry.",
        }

    if progress_cb:
        progress_cb({
            "phase": "installing_ollama",
            "percent": 100,
            "message": "Installing Ollama silently…",
        })

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

    return {"ok": False, "user_message": "Ollama install did not finish. Tap Retry to try again."}


def install_ollama(progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    if ollama_installed():
        return ensure_ollama_running()
    if platform.system() == "Windows":
        return install_ollama_windows(progress_cb)
    return {"ok": False, "user_message": "Automatic Ollama install is supported on Windows. Tap Retry or reinstall the app."}


def _format_pull_failure(
    model_name: str,
    *,
    error_code: str,
    returncode: Optional[int] = None,
    last_lines: Optional[List[str]] = None,
    exc: Optional[Exception] = None,
) -> Dict[str, Any]:
    detail_parts: List[str] = []
    if returncode is not None:
        detail_parts.append(f"Ollama exit code: {returncode}")
    if last_lines:
        detail_parts.extend(last_lines[-6:])
    if exc is not None:
        detail_parts.append(f"{type(exc).__name__}: {exc}")
    detail = "\n".join(p for p in detail_parts if p).strip()

    if error_code == "timeout":
        user = (
            f"Download of {model_name} exceeded 30 minutes and was stopped. "
            "Tap Retry to resume from the last checkpoint."
        )
    elif error_code == "network":
        user = f"Network error while downloading {model_name}. Check your connection and tap Retry."
    elif detail:
        user = f"Model download failed: {detail[:400]}"
    else:
        user = f"Model download failed for {model_name}. Tap Retry to resume."

    return {
        "ok": False,
        "model": model_name,
        "error_code": error_code,
        "error_detail": detail or user,
        "user_message": user,
        "resumable": has_resumable_pull(model_name),
    }


def pull_model(
    model_name: str,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    if not ollama_running():
        start = ensure_ollama_running()
        if not start.get("ok"):
            return {
                "ok": False,
                "error_code": "ollama_offline",
                "error_detail": start.get("user_message", "Ollama is offline."),
                "user_message": start.get("user_message", "Ollama is offline."),
            }

    cli = _ollama_cli()
    if not cli:
        return {
            "ok": False,
            "error_code": "cli_missing",
            "error_detail": "ollama executable not found on PATH",
            "user_message": "Ollama CLI not found. Tap Retry.",
        }

    resuming = has_resumable_pull(model_name)
    started_at = time.time()
    deadline = started_at + PULL_TIMEOUT_SEC
    est_mb = MODEL_SIZE_GB.get(model_name, 3.0) * 1024
    last_lines: List[str] = []
    last_pct = int(load_pull_state().get("percent") or 0) if resuming else 0

    if resuming and progress_cb:
        progress_cb({
            "phase": "pulling_model",
            "percent": last_pct,
            "model": model_name,
            "message": f"Resuming download of {model_name}…",
            "resuming": True,
        })

    try:
        proc = subprocess.Popen(
            [cli, "pull", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        line_queue: queue.Queue[str] = queue.Queue()

        def _reader() -> None:
            try:
                for raw in iter(proc.stdout.readline, ""):
                    line_queue.put(raw)
            finally:
                line_queue.put("")

        threading.Thread(target=_reader, daemon=True).start()

        while proc.poll() is None:
            if time.time() > deadline:
                proc.kill()
                proc.wait(timeout=10)
                save_pull_state(model_name, last_pct, error_code="timeout")
                return _format_pull_failure(
                    model_name,
                    error_code="timeout",
                    last_lines=last_lines,
                )
            try:
                raw = line_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            line = raw.strip()
            if not line:
                continue
            last_lines.append(line)
            if len(last_lines) > 20:
                last_lines.pop(0)

            lower = line.lower()
            if "error" in lower or "failed" in lower or "timeout" in lower:
                logger.warning("ollama pull: %s", line)

            parsed = _parse_pull_progress(line, model_name, started_at)
            last_pct = max(last_pct, int(parsed.get("percent") or 0))
            parsed["percent"] = last_pct
            parsed["phase"] = "pulling_model"
            parsed["resuming"] = resuming
            if "total_mb" not in parsed:
                parsed["total_mb"] = round(est_mb, 1)
            save_pull_state(model_name, last_pct)
            if progress_cb:
                progress_cb(parsed)

        # Drain remaining lines after process exit
        while True:
            try:
                raw = line_queue.get_nowait()
            except queue.Empty:
                break
            line = raw.strip()
            if line:
                last_lines.append(line)

        ok = proc.returncode == 0
        if ok:
            clear_pull_state()
            return {"ok": True, "model": model_name, "user_message": "Model ready."}

        err_code = "ollama_pull_failed"
        joined = " ".join(last_lines).lower()
        if "timeout" in joined or "deadline" in joined:
            err_code = "timeout"
        elif "connection" in joined or "network" in joined or "dial" in joined:
            err_code = "network"

        save_pull_state(model_name, last_pct, error_code=err_code, last_error=last_lines[-1] if last_lines else "")
        return _format_pull_failure(
            model_name,
            error_code=err_code,
            returncode=proc.returncode,
            last_lines=last_lines,
        )
    except FileNotFoundError as e:
        return _format_pull_failure(model_name, error_code="cli_missing", exc=e)
    except Exception as e:
        logger.exception("pull_model failed")
        save_pull_state(model_name, last_pct, error_code="exception", last_error=str(e))
        return _format_pull_failure(model_name, error_code="exception", exc=e, last_lines=last_lines)


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
