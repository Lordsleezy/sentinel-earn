"""Tests for hardware-based model recommendation."""
from sentinel_earn.hardware_probe import (
    recommend_earn_model,
    EARN_MODEL_14B,
    EARN_MODEL_7B,
    EARN_MODEL_3B,
    EARN_MODEL_1_5B,
)


def test_recommend_1_5b_low_ram():
    rec = recommend_earn_model(ram_gb=6, vram_gb=24, cpu_only=False, npu_present=False)
    assert rec["model"] == EARN_MODEL_1_5B


def test_recommend_14b_high_vram():
    rec = recommend_earn_model(ram_gb=32, vram_gb=16, cpu_only=False, npu_present=False)
    assert rec["model"] == EARN_MODEL_14B


def test_recommend_7b_8gb_vram():
    rec = recommend_earn_model(ram_gb=16, vram_gb=8, cpu_only=False, npu_present=False)
    assert rec["model"] == EARN_MODEL_7B


def test_recommend_3b_cpu_only():
    rec = recommend_earn_model(ram_gb=16, vram_gb=0, cpu_only=True, npu_present=False)
    assert rec["model"] == EARN_MODEL_3B


def test_recommend_3b_npu():
    rec = recommend_earn_model(ram_gb=16, vram_gb=12, cpu_only=False, npu_present=True)
    assert rec["model"] == EARN_MODEL_3B


def test_recommend_3b_4gb_vram():
    rec = recommend_earn_model(ram_gb=16, vram_gb=4, cpu_only=False, npu_present=False)
    assert rec["model"] == EARN_MODEL_3B


def test_explanation_includes_model_name():
    rec = recommend_earn_model(ram_gb=16, vram_gb=0, cpu_only=True, npu_present=False)
    assert EARN_MODEL_3B in rec["explanation"]
