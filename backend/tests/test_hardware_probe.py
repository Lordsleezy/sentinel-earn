"""Tests for hardware-based model recommendation."""
from sentinel_earn.hardware_probe import recommend_earn_model, EARN_MODEL_HIGH, EARN_MODEL_LOW


def test_recommend_14b_on_high_ram():
    rec = recommend_earn_model(ram_gb=32, vram_gb=0)
    assert rec["model"] == EARN_MODEL_HIGH


def test_recommend_14b_on_high_vram():
    rec = recommend_earn_model(ram_gb=8, vram_gb=12)
    assert rec["model"] == EARN_MODEL_HIGH


def test_recommend_7b_on_low_spec():
    rec = recommend_earn_model(ram_gb=8, vram_gb=4)
    assert rec["model"] == EARN_MODEL_LOW
