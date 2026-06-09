"""Hardware detection for Sentinel Earn — ported from SentinelAI onboarding probes."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Callable, Dict, Optional, TypeVar

T = TypeVar("T")

_NVIDIA_TIMEOUT = 3.0
_HTTP_TIMEOUT = 2.0
_RAM_TIMEOUT = 2.0

EARN_MODEL_HIGH = "qwen2.5-coder:14b"
EARN_MODEL_LOW = "qwen2.5-coder:7b"


def _run_timed(fn: Callable[[], T], timeout: float, default: T) -> T:
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="earn-probe") as pool:
            fut = pool.submit(fn)
            return fut.result(timeout=timeout)
    except (FuturesTimeout, Exception):
        return default


def _probe_ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return 8.0


def _probe_cpu_name() -> str:
    try:
        return platform.processor() or platform.machine() or "Unknown CPU"
    except Exception:
        return "Unknown CPU"


def _probe_gpu_nvidia() -> Dict[str, Any]:
    out: Dict[str, Any] = {"gpu_name": "CPU inference", "vram_gb": 0.0, "source": "cpu_fallback"}
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=int(_NVIDIA_TIMEOUT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            line = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            out["gpu_name"] = parts[0] if parts else "NVIDIA GPU"
            if len(parts) > 1:
                try:
                    out["vram_gb"] = round(float(parts[1]) / 1024, 1)
                except ValueError:
                    out["vram_gb"] = 0.0
            out["source"] = "nvidia-smi"
    except Exception:
        pass
    return out


def _npu_tops_from_name(name: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*tops", name, re.I)
    return int(m.group(1)) if m else None


def _probe_npu() -> Dict[str, Any]:
    if sys.platform != "win32":
        return {"npu_present": False, "tops": None, "device_name": None}
    ps_script = (
        "Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue | "
        "Where-Object { "
        "$_.Name -match 'NPU|Neural Processing|AI Boost|XDNA|Hexagon.*NPU|Intel.*NPU|Qualcomm.*NPU' "
        "-and $_.Name -notmatch 'Bluetooth|XINPUT|Audio|Speaker|Microphone' "
        "} | Select-Object -First 5 -ExpandProperty Name"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if not lines:
            return {"npu_present": False, "tops": None, "device_name": None}
        tops = None
        for ln in lines:
            parsed = _npu_tops_from_name(ln)
            if parsed is not None:
                tops = parsed
                break
        return {"npu_present": True, "tops": tops, "device_name": lines[0]}
    except Exception:
        return {"npu_present": False, "tops": None, "device_name": None}


def _probe_ollama_installed() -> bool:
    if shutil.which("ollama"):
        return True
    if platform.system() == "Windows":
        local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")
        for name in ("ollama.exe", "Ollama.exe"):
            if os.path.isfile(os.path.join(local, name)):
                return True
    return False


def _probe_ollama_running(host: str = "http://127.0.0.1:11434") -> bool:
    try:
        import httpx
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=_HTTP_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def recommend_earn_model(ram_gb: float, vram_gb: float) -> Dict[str, Any]:
    """Pick qwen2.5-coder:14b on capable hardware, else 7b."""
    use_14b = ram_gb >= 16.0 or vram_gb >= 8.0
    model = EARN_MODEL_HIGH if use_14b else EARN_MODEL_LOW
    return {
        "model": model,
        "tier": "high" if use_14b else "low",
        "ram_gb": ram_gb,
        "vram_gb": vram_gb,
        "reason": (
            "16GB+ RAM or 8GB+ VRAM detected"
            if use_14b
            else "Using lighter model for this machine"
        ),
    }


def probe_machine_profile(ollama_host: str = "http://127.0.0.1:11434") -> Dict[str, Any]:
    ram_gb = _run_timed(_probe_ram_gb, _RAM_TIMEOUT, 8.0)
    gpu = _run_timed(_probe_gpu_nvidia, _NVIDIA_TIMEOUT, {
        "gpu_name": "CPU inference",
        "vram_gb": 0.0,
        "source": "cpu_fallback",
    })
    npu = _run_timed(_probe_npu, 15.0, {"npu_present": False, "tops": None, "device_name": None})
    ollama_installed = _run_timed(_probe_ollama_installed, 1.0, False)
    ollama_running = _run_timed(lambda: _probe_ollama_running(ollama_host), _HTTP_TIMEOUT + 0.5, False)
    rec = recommend_earn_model(ram_gb, float(gpu.get("vram_gb") or 0))

    return {
        "os": platform.system(),
        "cpu_name": _probe_cpu_name(),
        "ram_gb": ram_gb,
        "vram_gb": gpu.get("vram_gb", 0.0),
        "gpu_name": gpu.get("gpu_name", "CPU inference"),
        "gpu_probe_source": gpu.get("source", "unknown"),
        "npu_present": npu.get("npu_present", False),
        "npu_device": npu.get("device_name"),
        "ollama_installed": ollama_installed,
        "ollama_running": ollama_running,
        "recommended_model": rec["model"],
        "model_recommendation": rec,
    }
