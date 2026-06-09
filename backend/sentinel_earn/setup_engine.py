"""First-launch setup: hardware probe → Ollama install → model pull."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Optional

from sentinel_earn.config import load_settings, save_settings
from sentinel_earn import hardware_probe, ollama_runtime

logger = logging.getLogger("sentinel_earn.setup")

_lock = threading.Lock()
_instance: Optional["SetupEngine"] = None


class SetupEngine:
    def __init__(self) -> None:
        self._state: Dict[str, Any] = {
            "phase": "idle",
            "percent": 0,
            "message": "",
            "complete": False,
            "error": None,
            "action": None,
            "url": None,
            "hardware": {},
            "model": None,
        }
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def snapshot(self) -> Dict[str, Any]:
        with _lock:
            return dict(self._state)

    def _set(self, **kwargs: Any) -> None:
        with _lock:
            self._state.update(kwargs)

    def start_background(self) -> None:
        with _lock:
            if self._running or self._thread:
                return
            if ollama_runtime.is_setup_complete():
                settings = load_settings()
                ready = ollama_runtime.load_ready()
                self._state.update({
                    "phase": "ready",
                    "percent": 100,
                    "message": "Sentinel Earn engine is ready.",
                    "complete": True,
                    "model": ready.get("model") or settings.get("ollama_model"),
                })
                return
            self._running = True
            self._thread = threading.Thread(target=self._run, name="earn-setup", daemon=True)
            self._thread.start()

    def retry(self) -> None:
        with _lock:
            if self._running:
                return
            self._state = {
                "phase": "idle",
                "percent": 0,
                "message": "Retrying setup…",
                "complete": False,
                "error": None,
                "action": None,
                "url": None,
                "hardware": {},
                "model": None,
            }
            self._running = True
            self._thread = threading.Thread(target=self._run, name="earn-setup-retry", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            self._set(phase="detecting", percent=5, message="Detecting hardware…")
            host = load_settings().get("ollama_host", "http://127.0.0.1:11434")
            profile = hardware_probe.probe_machine_profile(host)
            rec = profile.get("model_recommendation") or {}
            model = rec.get("model") or hardware_probe.EARN_MODEL_LOW
            self._set(
                hardware=profile,
                model=model,
                message=f"Recommended model: {model}",
                percent=10,
            )

            if not profile.get("ollama_installed"):
                self._set(phase="installing_ollama", percent=15, message="Downloading Ollama…")

                def install_cb(info: Dict[str, Any]) -> None:
                    dl = int(info.get("download_percent") or 0)
                    pct = 15 + int(dl * 0.25)
                    self._set(
                        percent=min(pct, 40),
                        message=info.get("message") or "Installing Ollama…",
                    )

                result = ollama_runtime.install_ollama(install_cb)
                if not result.get("ok"):
                    self._fail(result)
                    return
            else:
                self._set(phase="starting_ollama", percent=35, message="Starting Ollama…")
                result = ollama_runtime.ensure_ollama_running()
                if not result.get("ok"):
                    self._fail(result)
                    return

            self._set(phase="pulling_model", percent=45, message=f"Downloading {model}…")

            def pull_cb(info: Dict[str, Any]) -> None:
                pull_pct = int(info.get("percent") or 0)
                pct = 45 + int(pull_pct * 0.5)
                self._set(
                    percent=min(pct, 95),
                    message=info.get("message") or f"Downloading {model}…",
                )

            if not ollama_runtime.model_present(model):
                pull = ollama_runtime.pull_model(model, pull_cb)
                if not pull.get("ok"):
                    self._fail(pull)
                    return

            settings = load_settings()
            settings["ollama_model"] = model
            save_settings(settings)

            ollama_runtime.save_ready(model, profile)
            self._set(
                phase="ready",
                percent=100,
                message="Sentinel Earn engine is ready.",
                complete=True,
                model=model,
            )
        except Exception as e:
            logger.exception("setup failed")
            self._fail({"user_message": str(e)[:200], "action": "retry"})
        finally:
            with _lock:
                self._running = False

    def _fail(self, result: Dict[str, Any]) -> None:
        self._set(
            phase="error",
            complete=False,
            error=result.get("user_message") or result.get("error") or "Setup failed.",
            action=result.get("action"),
            url=result.get("url"),
            message=result.get("user_message") or "Setup could not complete automatically.",
        )


def get_setup_engine() -> SetupEngine:
    global _instance
    if _instance is None:
        _instance = SetupEngine()
    return _instance
