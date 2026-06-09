"""First-launch setup: detect hardware → user downloads → Ollama + model pull."""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from sentinel_earn.config import load_settings, save_settings
from sentinel_earn import hardware_probe, ollama_runtime

logger = logging.getLogger("sentinel_earn.setup")

_lock = threading.Lock()
_instance: Optional["SetupEngine"] = None


def _empty_state() -> Dict[str, Any]:
    return {
        "phase": "idle",
        "percent": 0,
        "message": "",
        "complete": False,
        "error": None,
        "hardware": {},
        "hardware_summary": {},
        "model": None,
        "recommendation": None,
        "recommendation_text": "",
        "downloaded_mb": None,
        "total_mb": None,
        "eta_seconds": None,
        "awaiting_user": False,
        "error_detail": None,
        "error_code": None,
        "resumable": False,
    }


class SetupEngine:
    def __init__(self) -> None:
        self._state = _empty_state()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def snapshot(self) -> Dict[str, Any]:
        with _lock:
            return dict(self._state)

    def _set(self, **kwargs: Any) -> None:
        with _lock:
            self._state.update(kwargs)

    def initialize(self) -> None:
        """On backend start: skip if done, else detect hardware and wait for user."""
        with _lock:
            if self._running:
                return
            if ollama_runtime.is_setup_complete():
                settings = load_settings()
                ready = ollama_runtime.load_ready()
                profile = ready.get("profile") or {}
                rec = profile.get("model_recommendation") or {}
                self._state.update({
                    "phase": "ready",
                    "percent": 100,
                    "message": "Sentinel Earn is ready.",
                    "complete": True,
                    "model": ready.get("model") or settings.get("ollama_model"),
                    "hardware": profile,
                    "hardware_summary": profile.get("hardware_summary") or {},
                    "recommendation": rec,
                    "recommendation_text": rec.get("explanation", ""),
                    "awaiting_user": False,
                })
                return
        self._detect_hardware()

    def _detect_hardware(self) -> None:
        with _lock:
            if self._running:
                return
            self._running = True
        try:
            self._set(phase="detecting", percent=0, message="Detecting your hardware…", error=None)
            host = load_settings().get("ollama_host", "http://127.0.0.1:11434")
            profile = hardware_probe.probe_machine_profile(host)
            rec = profile.get("model_recommendation") or {}
            model = rec.get("model") or hardware_probe.EARN_MODEL_3B
            self._set(
                phase="awaiting_download",
                percent=0,
                message="Ready to download",
                hardware=profile,
                hardware_summary=profile.get("hardware_summary") or {},
                model=model,
                recommendation=rec,
                recommendation_text=rec.get("explanation") or f"Based on your hardware we recommend {model}.",
                awaiting_user=True,
                complete=False,
            )
        except Exception as e:
            logger.exception("hardware detect failed")
            self._fail({"user_message": f"Hardware detection failed: {str(e)[:120]}"})
        finally:
            with _lock:
                self._running = False

    def start_download(self) -> bool:
        with _lock:
            if self._running:
                return False
            if self._state.get("complete"):
                return False
            self._running = True
            self._thread = threading.Thread(target=self._run_download, name="earn-download", daemon=True)
            self._thread.start()
            return True

    def retry(self) -> None:
        with _lock:
            if self._running:
                return
            ollama_runtime.clear_ready()
            self._state = _empty_state()
        self._detect_hardware()
        # Auto-start download after re-detection (user asked for full flow on retry)
        def _wait_and_download() -> None:
            import time
            for _ in range(30):
                snap = self.snapshot()
                if snap.get("phase") == "awaiting_download":
                    self.start_download()
                    return
                if snap.get("phase") == "error":
                    return
                time.sleep(0.2)
        threading.Thread(target=_wait_and_download, name="earn-retry-download", daemon=True).start()

    def _run_download(self) -> None:
        try:
            snap = self.snapshot()
            profile = snap.get("hardware") or {}
            rec = profile.get("model_recommendation") or snap.get("recommendation") or {}
            model = rec.get("model") or snap.get("model") or hardware_probe.EARN_MODEL_3B

            self._set(
                phase="installing_ollama",
                awaiting_user=False,
                percent=5,
                message="Checking Ollama…",
                model=model,
            )

            if not profile.get("ollama_installed"):
                def install_cb(info: Dict[str, Any]) -> None:
                    pct = int(info.get("percent") or 0)
                    self._set(
                        phase="installing_ollama",
                        percent=min(5 + int(pct * 0.3), 35),
                        message=info.get("message") or "Installing Ollama…",
                        downloaded_mb=info.get("downloaded_mb"),
                        total_mb=info.get("total_mb"),
                        eta_seconds=info.get("eta_seconds"),
                    )

                result = ollama_runtime.install_ollama(install_cb)
                if not result.get("ok"):
                    self._fail(result)
                    return
            else:
                self._set(phase="starting_ollama", percent=30, message="Starting Ollama…")
                result = ollama_runtime.ensure_ollama_running()
                if not result.get("ok"):
                    self._fail(result)
                    return

            self._set(
                phase="pulling_model",
                percent=40,
                message=f"Downloading {model}…",
                downloaded_mb=0,
                total_mb=ollama_runtime.MODEL_SIZE_GB.get(model, 3.0) * 1024,
            )

            if not ollama_runtime.model_present(model):
                resuming = ollama_runtime.has_resumable_pull(model)
                self._set(
                    message=f"Resuming download of {model}…" if resuming else f"Downloading {model}…",
                )

                def pull_cb(info: Dict[str, Any]) -> None:
                    pull_pct = int(info.get("percent") or 0)
                    msg = info.get("message") or (
                        f"Resuming download of {model}…" if info.get("resuming") else f"Downloading {model}…"
                    )
                    self._set(
                        phase="pulling_model",
                        percent=min(40 + int(pull_pct * 0.55), 98),
                        message=msg,
                        model=model,
                        downloaded_mb=info.get("downloaded_mb"),
                        total_mb=info.get("total_mb"),
                        eta_seconds=info.get("eta_seconds"),
                    )

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
                message="Sentinel Earn is ready.",
                complete=True,
                model=model,
                downloaded_mb=None,
                total_mb=None,
                eta_seconds=None,
            )
        except Exception as e:
            logger.exception("download failed")
            self._fail({"user_message": str(e)[:200]})
        finally:
            with _lock:
                self._running = False

    def _fail(self, result: Dict[str, Any]) -> None:
        detail = result.get("error_detail") or result.get("error") or ""
        summary = result.get("user_message") or detail or "Setup failed."
        self._set(
            phase="error",
            complete=False,
            awaiting_user=False,
            error=summary,
            error_detail=detail if detail != summary else None,
            error_code=result.get("error_code"),
            resumable=bool(result.get("resumable")),
            message=summary,
        )


def get_setup_engine() -> SetupEngine:
    global _instance
    if _instance is None:
        _instance = SetupEngine()
    return _instance
