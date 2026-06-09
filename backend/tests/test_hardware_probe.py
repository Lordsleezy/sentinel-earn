"""Tests for hardware-based model recommendation."""
from sentinel_earn.hardware_probe import (
    recommend_earn_model,
    is_discrete_gpu,
    EARN_MODEL_14B,
    EARN_MODEL_7B,
    EARN_MODEL_3B,
    EARN_MODEL_1_5B,
)


def test_is_discrete_gpu_nvidia_smi():
    assert is_discrete_gpu("NVIDIA RTX 4090", "NVIDIA", "nvidia-smi")


def test_is_discrete_gpu_integrated_intel():
    assert not is_discrete_gpu("Intel(R) UHD Graphics 770", "Intel", "win32_videocontroller")


def test_is_discrete_gpu_snapdragon_adreno():
    assert not is_discrete_gpu("Qualcomm(R) Adreno X1-85 GPU", "Qualcomm", "win32_videocontroller")


def test_recommend_1_5b_no_gpu_low_ram():
    rec = recommend_earn_model(
        ram_gb=12, discrete_vram_gb=0, has_discrete_gpu=False, cpu_only=True,
    )
    assert rec["model"] == EARN_MODEL_1_5B


def test_recommend_3b_no_gpu_16gb_ram():
    rec = recommend_earn_model(
        ram_gb=32, discrete_vram_gb=0, has_discrete_gpu=False, cpu_only=True, npu_present=True,
    )
    assert rec["model"] == EARN_MODEL_3B


def test_recommend_14b_discrete_16gb_vram():
    rec = recommend_earn_model(
        ram_gb=32, discrete_vram_gb=16, has_discrete_gpu=True, cpu_only=False,
    )
    assert rec["model"] == EARN_MODEL_14B


def test_recommend_7b_only_with_discrete_8gb_vram():
    rec = recommend_earn_model(
        ram_gb=32, discrete_vram_gb=8, has_discrete_gpu=True, cpu_only=False,
    )
    assert rec["model"] == EARN_MODEL_7B


def test_no_7b_without_discrete_gpu_even_with_fake_vram():
    """Snapdragon may report integrated VRAM — must not pick 7B without discrete GPU."""
    rec = recommend_earn_model(
        ram_gb=32, discrete_vram_gb=12, has_discrete_gpu=False, cpu_only=True,
    )
    assert rec["model"] == EARN_MODEL_3B


def test_recommend_3b_discrete_low_vram():
    rec = recommend_earn_model(
        ram_gb=16, discrete_vram_gb=4, has_discrete_gpu=True, cpu_only=False,
    )
    assert rec["model"] == EARN_MODEL_3B


def test_explanation_includes_model_name():
    rec = recommend_earn_model(
        ram_gb=32, discrete_vram_gb=0, has_discrete_gpu=False, cpu_only=True,
    )
    assert EARN_MODEL_3B in rec["explanation"]
