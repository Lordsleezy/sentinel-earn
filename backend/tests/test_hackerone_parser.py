"""Tests for HackerOne program parsing."""
from sentinel_earn.hackerone_parser import parse_hackerone_program


def test_parse_bounty_program():
    raw = {
        "name": "Acme Corp",
        "handle": "acme",
        "offers_bounties": True,
        "url": "https://hackerone.com/acme",
        "targets": {
            "in_scope": [
                {
                    "asset_identifier": "api.acme.com",
                    "asset_type": "URL",
                    "eligible_for_bounty": True,
                    "max_severity": "critical",
                }
            ],
            "out_of_scope": [],
        },
        "attributes": {"maximum_bounty_table": {"critical": 10000}},
    }
    parsed = parse_hackerone_program(raw)
    assert parsed["handle"] == "acme"
    assert parsed["offers_bounties"] is True
    assert parsed["scope_count"] == 1
