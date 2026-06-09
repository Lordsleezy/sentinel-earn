"""Tests for configuration helpers."""
import os

from sentinel_earn.config import github_token_configured


def test_github_token_configured_from_settings():
    assert github_token_configured({"github_token": "ghp_test123"}) is True


def test_github_token_configured_from_env(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_env")
    assert github_token_configured({"github_token": ""}) is True


def test_github_token_not_configured(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert github_token_configured({"github_token": ""}) is False
