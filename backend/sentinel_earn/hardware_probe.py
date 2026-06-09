"""Hardware detection for Sentinel Earn — CPU, GPU, NPU, model recommendation."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Callable, Dict, List, Optional, TypeVar

T = TypeVar("T")

_PROBE_TIMEOUT = 3.0
_RAM_TIMEOUT = 2.0
_NPU_TIMEOUT = 15.0
_HTTP_TIMEOUT = 2.0

EARN_MODEL_14B = "qwen2.5-coder:14b"
EARN_MODEL_7B = "qwen2.5-coder:7b"
EARN_MODEL_3B = "qwen2.5-coder:3b"
EARN_MODEL_1_5B = "qwen2.5-coder:1.5b"


def _run_timed(fn: Callable[[], T], timeout: float, default: T) -> T:
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="earn-probe") as pool:
            return pool.submit(fn).result(timeout=timeout)
    except (FuturesTimeout, Exception):
        return default


def _probe_cpu_cores() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=True) or 1
    except Exception:
        return 1


def _probe_ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return 8.0


def _probe_cpu_name() -> str:
    try:
        name = platform.processor() or ""
        if name.strip():
            return name.strip()
        import psutil
        freq = psutil.cpu_freq()
        if freq and freq.max:
            return f"{_probe_cpu_cores()}-core CPU @ {round(freq.max)} MHz"
        return f"{_probe_cpu_cores()}-core CPU"
    except Exception:
        return f"{_probe_cpu_cores()}-core CPU"


def _probe_nvidia_gpu() -> Optional[Dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=int(_PROBE_TIMEOUT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            line = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            vram = 0.0
            if len(parts) > 1:
                try:
                    vram = round(float(parts[1]) / 1024, 1)
                except ValueError:
                    pass
            return {
                "gpu_name": parts[0] if parts else "NVIDIA GPU",
                "vram_gb": vram,
                "vendor": "NVIDIA",
                "source": "nvidia-smi",
            }
    except Exception:
        pass
    return None


_INTEGRATED_GPU_PATTERNS = re.compile(
    r"intel|uhd|iris|hd graphics|adreno|qualcomm|snapdragon|microsoft basic|"
    r"remote display|virtual display|parsec|vmware|citrix|standard vga|"
    r"radeon\(tm\) graphics$|amd radeon graphics$",
    re.I,
)


def is_discrete_gpu(gpu_name: str, vendor: str = "", source: str = "") -> bool:
    """True only for a real discrete GPU suitable for large model inference."""
    if source == "nvidia-smi":
        return True
    name = (gpu_name or "").strip()
    if not name or name.lower() in ("cpu only", "cpu inference"):
        return False
    if _INTEGRATED_GPU_PATTERNS.search(name):
        return False
    upper = name.upper()
    if vendor == "NVIDIA" or "GEFORCE" in upper or "RTX" in upper or "GTX" in upper or "QUADRO" in upper:
        return True
    if vendor == "AMD" and re.search(r"\bRX\s*\d|RADEON\s*RX|RADEON\s*PRO", upper):
        return True
    return False


def _probe_gpus_windows() -> List[Dict[str, Any]]:
    gpus: List[Dict[str, Any]] = []
    nvidia = _probe_nvidia_gpu()
    if nvidia:
        nvidia["discrete"] = True
        gpus.append(nvidia)

    ps_script = (
        "Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Name -and $_.Name -notmatch 'Microsoft|Remote|Virtual|Parsec|Mirror' } | "
        "Select-Object Name, AdapterRAM | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=int(_PROBE_TIMEOUT) + 2,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            return gpus
        import json
        raw = json.loads(proc.stdout)
        entries = raw if isinstance(raw, list) else [raw]
        for entry in entries:
            name = (entry.get("Name") or "").strip()
            if not name:
                continue
            if nvidia and name.lower() in (nvidia.get("gpu_name") or "").lower():
                continue
            adapter_ram = entry.get("AdapterRAM") or 0
            vram_gb = 0.0
            try:
                if adapter_ram and int(adapter_ram) > 0:
                    vram_gb = round(int(adapter_ram) / (1024 ** 3), 1)
            except (TypeError, ValueError):
                pass
            vendor = "Unknown"
            upper = name.upper()
            if "AMD" in upper or "RADEON" in upper:
                vendor = "AMD"
            elif "INTEL" in upper or "IRIS" in upper or "UHD" in upper:
                vendor = "Intel"
            elif "NVIDIA" in upper or "GEFORCE" in upper or "RTX" in upper or "GTX" in upper:
                vendor = "NVIDIA"
            gpus.append({
                "gpu_name": name,
                "vram_gb": vram_gb,
                "vendor": vendor,
                "source": "win32_videocontroller",
                "discrete": is_discrete_gpu(name, vendor, "win32_videocontroller"),
            })
    except Exception:
        pass
    return gpus


def _probe_gpu() -> Dict[str, Any]:
    if sys.platform == "win32":
        gpus = _probe_gpus_windows()
    else:
        gpus = []
        nvidia = _probe_nvidia_gpu()
        if nvidia:
            nvidia["discrete"] = True
            gpus.append(nvidia)

    discrete_gpus = [g for g in gpus if g.get("discrete")]

    if not discrete_gpus:
        display = gpus[0] if gpus else None
        return {
            "gpu_name": display.get("gpu_name", "CPU only") if display else "CPU only",
            "vram_gb": 0.0,
            "discrete_vram_gb": 0.0,
            "vendor": display.get("vendor", "None") if display else "None",
            "source": display.get("source", "cpu_fallback") if display else "cpu_fallback",
            "cpu_only": True,
            "has_discrete_gpu": False,
            "gpus": gpus,
        }

    best = max(discrete_gpus, key=lambda g: float(g.get("vram_gb") or 0))
    return {
        "gpu_name": best.get("gpu_name", "GPU"),
        "vram_gb": float(best.get("vram_gb") or 0),
        "discrete_vram_gb": float(best.get("vram_gb") or 0),
        "vendor": best.get("vendor", "Unknown"),
        "source": best.get("source", "unknown"),
        "cpu_only": False,
        "has_discrete_gpu": True,
        "gpus": gpus,
    }


def _npu_tops_from_name(name: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*tops", name, re.I)
    return int(m.group(1)) if m else None


def _probe_npu() -> Dict[str, Any]:
    if sys.platform != "win32":
        return {"npu_present": False, "tops": None, "device_name": None, "vendor": None}
    ps_script = (
        "Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue | "
        "Where-Object { "
        "$_.Name -match 'NPU|Neural Processing|AI Boost|XDNA|Hexagon.*NPU|Intel.*NPU|Qualcomm.*NPU|Snapdragon' "
        "-and $_.Name -notmatch 'Bluetooth|XINPUT|Audio|Speaker|Microphone' "
        "} | Select-Object -First 5 -ExpandProperty Name"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=int(_NPU_TIMEOUT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if not lines:
            return {"npu_present": False, "tops": None, "device_name": None, "vendor": None}
        device = lines[0]
        tops = None
        for ln in lines:
            parsed = _npu_tops_from_name(ln)
            if parsed is not None:
                tops = parsed
                break
        vendor = "Qualcomm" if re.search(r"qualcomm|snapdragon|hexagon", device, re.I) else "NPU"
        return {"npu_present": True, "tops": tops, "device_name": device, "vendor": vendor}
    except Exception:
        return {"npu_present": False, "tops": None, "device_name": None, "vendor": None}


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


def recommend_earn_model(
    ram_gb: float,
    discrete_vram_gb: float,
    *,
    has_discrete_gpu: bool = False,
    cpu_only: bool = False,
    npu_present: bool = False,
) -> Dict[str, Any]:
    """Tiered model selection — 7B/14B only with a real discrete GPU."""
    if not has_discrete_gpu or cpu_only:
        if ram_gb < 16.0:
            model = EARN_MODEL_1_5B
            reason = (
                f"No discrete GPU detected ({ram_gb}GB RAM) — "
                f"the 1.5B model runs reliably on CPU."
            )
            tier = "minimal"
        else:
            model = EARN_MODEL_3B
            if npu_present:
                reason = (
                    "No discrete GPU — your NPU/CPU system runs best with the "
                    "2GB 3B model for stable inference."
                )
            else:
                reason = (
                    f"No discrete GPU ({ram_gb}GB RAM) — "
                    f"the 3B model is optimized for CPU inference."
                )
            tier = "balanced"
    elif discrete_vram_gb >= 16.0:
        model = EARN_MODEL_14B
        reason = f"Your {discrete_vram_gb}GB VRAM GPU can run the largest coder model."
        tier = "ultra"
    elif discrete_vram_gb >= 8.0:
        model = EARN_MODEL_7B
        reason = f"Your {discrete_vram_gb}GB VRAM GPU is a great fit for the 7B coder model."
        tier = "high"
    else:
        model = EARN_MODEL_3B
        reason = f"Your GPU has {discrete_vram_gb}GB VRAM — the efficient 3B model is recommended."
        tier = "balanced"

    return {
        "model": model,
        "tier": tier,
        "reason": reason,
        "explanation": f"Based on your hardware we recommend {model} — optimized for your device.",
        "ram_gb": ram_gb,
        "vram_gb": discrete_vram_gb,
        "discrete_vram_gb": discrete_vram_gb,
        "has_discrete_gpu": has_discrete_gpu,
        "cpu_only": cpu_only or not has_discrete_gpu,
        "npu_present": npu_present,
    }


def format_hardware_summary(profile: Dict[str, Any]) -> Dict[str, str]:
    """Human-readable hardware lines for the setup UI."""
    cpu = profile.get("cpu_name") or "Unknown CPU"
    cores = profile.get("cpu_cores") or "?"
    ram = profile.get("ram_gb") or "?"
    gpu = profile.get("gpu_name") or "CPU only"
    vram = profile.get("vram_gb") or 0
    vendor = profile.get("gpu_vendor") or ""

    gpu_line = f"{gpu}"
    if vram and float(vram) > 0:
        gpu_line += f" ({vram}GB VRAM)"
    elif profile.get("cpu_only") or not profile.get("has_discrete_gpu"):
        gpu_line = "No discrete GPU — CPU / NPU inference"

    npu_line = None
    if profile.get("npu_present"):
        npu_line = profile.get("npu_device") or "Neural Processing Unit detected"
        if profile.get("npu_vendor"):
            npu_line = f"{profile['npu_vendor']} NPU — {npu_line}"

    return {
        "cpu": f"{cpu} ({cores} cores)",
        "ram": f"{ram} GB system memory",
        "gpu": gpu_line if not profile.get("npu_present") or vendor != "None" else gpu_line,
        "npu": npu_line or "",
        "accelerator": npu_line or gpu_line,
    }


def probe_machine_profile(ollama_host: str = "http://127.0.0.1:11434") -> Dict[str, Any]:
    ram_gb = _run_timed(_probe_ram_gb, _RAM_TIMEOUT, 8.0)
    cpu_cores = _run_timed(_probe_cpu_cores, 1.0, 1)
    gpu = _run_timed(_probe_gpu, _PROBE_TIMEOUT + 2, {
        "gpu_name": "CPU only",
        "vram_gb": 0.0,
        "vendor": "None",
        "source": "cpu_fallback",
        "cpu_only": True,
        "gpus": [],
    })
    npu = _run_timed(_probe_npu, _NPU_TIMEOUT, {
        "npu_present": False,
        "tops": None,
        "device_name": None,
        "vendor": None,
    })
    ollama_installed = _run_timed(_probe_ollama_installed, 1.0, False)
    ollama_running = _run_timed(lambda: _probe_ollama_running(ollama_host), _HTTP_TIMEOUT + 0.5, False)

    rec = recommend_earn_model(
        ram_gb,
        float(gpu.get("discrete_vram_gb") or 0),
        has_discrete_gpu=bool(gpu.get("has_discrete_gpu")),
        cpu_only=bool(gpu.get("cpu_only")),
        npu_present=bool(npu.get("npu_present")),
    )

    profile = {
        "os": platform.system(),
        "cpu_name": _probe_cpu_name(),
        "cpu_cores": cpu_cores,
        "ram_gb": ram_gb,
        "vram_gb": gpu.get("discrete_vram_gb", 0.0),
        "discrete_vram_gb": gpu.get("discrete_vram_gb", 0.0),
        "has_discrete_gpu": gpu.get("has_discrete_gpu", False),
        "gpu_name": gpu.get("gpu_name", "CPU only"),
        "gpu_vendor": gpu.get("vendor", "None"),
        "gpu_probe_source": gpu.get("source", "unknown"),
        "cpu_only": gpu.get("cpu_only", True),
        "gpus": gpu.get("gpus", []),
        "npu_present": npu.get("npu_present", False),
        "npu_device": npu.get("device_name"),
        "npu_vendor": npu.get("vendor"),
        "ollama_installed": ollama_installed,
        "ollama_running": ollama_running,
        "recommended_model": rec["model"],
        "model_recommendation": rec,
        "hardware_summary": {},
    }
    profile["hardware_summary"] = format_hardware_summary(profile)
    return profile
