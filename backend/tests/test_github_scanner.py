from sentinel_earn import github_scanner


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_find_returns_correct_structure(monkeypatch):
    payload = {
        "items": [
            {
                "number": 1,
                "title": "Fix Python tests",
                "html_url": "https://github.com/o/r/issues/1",
                "repository_url": "https://api.github.com/repos/o/r",
                "body": "Please add tests",
                "labels": [{"name": "bounty"}],
                "comments": 1,
            }
        ]
    }

    def fake_get(url, *a, **k):
        if "search/issues" in url and "type:pr" not in (k.get("params") or {}).get("q", ""):
            return FakeResponse(payload)
        if url.endswith("/readme") or "/contents/tests" in url:
            return FakeResponse({})
        if "/contents/" in url:
            raise RuntimeError("missing")
        if "/repos/o/r" in url:
            return FakeResponse({"language": "Python"})
        return FakeResponse({"total_count": 0, "items": []})

    monkeypatch.setattr(github_scanner.requests, "get", fake_get)
    issue = github_scanner.find_bounty_issues()[0]
    assert issue["title"] == "Fix Python tests"
    assert issue["repo_url"] == "https://github.com/o/r"
    assert issue["labels"] == ["bounty"]


def test_score_ranks_python_tests_higher():
    good = {
        "title": "Python bug",
        "body": "README includes pytest tests and clear steps " * 5,
        "labels": ["good-first-issue"],
        "has_tests": True,
        "has_readme": True,
    }
    weak = {"title": "Bug", "body": "broken", "labels": [], "competing_prs": True}
    assert github_scanner.score_issue(good) > github_scanner.score_issue(weak)


def test_pipeline_cycle_runs_without_exception(monkeypatch):
    monkeypatch.setattr(
        github_scanner,
        "find_bounty_issues",
        lambda max=20: [{
            "title": "Python tests",
            "url": "https://github.com/o/r/issues/2",
            "repo_url": "https://github.com/o/r",
            "body": "pytest tests README " * 10,
            "labels": ["bounty"],
            "has_readme": True,
            "has_tests": True,
            "open_prs_on_issue": 0,
            "language": "Python",
        }],
    )
    result = github_scanner.run_pipeline_cycle()
    assert result["found"] == 1
    assert result["queued"] == 1
    assert len(result["top_issues"]) == 1
