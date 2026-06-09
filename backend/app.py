"""
Sentinel Earn — Flask API backend (standalone).
"""
from __future__ import annotations

import logging
import os
from flask import Flask, jsonify, request
from flask_cors import CORS

from sentinel_earn.config import apply_settings, load_settings, save_settings
from sentinel_earn import db
from sentinel_earn.pipeline import (
    get_dashboard,
    scan_github_bounties,
    scan_hackerone_programs,
    run_full_cycle,
    approve_opportunity,
    generate_patch_for_opportunity,
    submit_patch,
    start_background_worker,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sentinel_earn.api")

apply_settings()
db.init_db()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

start_background_worker(interval_sec=45)


@app.route("/api/ping")
def ping():
    return jsonify({"status": "ok", "app": "Sentinel Earn"})


@app.route("/api/dashboard")
def dashboard():
    return jsonify(get_dashboard())


@app.route("/api/bounties")
def bounties():
    return jsonify({"opportunities": db.list_opportunities(limit=200)})


@app.route("/api/bounties/scan/github", methods=["POST"])
def scan_github():
    max_issues = int((request.json or {}).get("max", 20))
    return jsonify(scan_github_bounties(max_issues=max_issues))


@app.route("/api/bounties/scan/hackerone", methods=["POST"])
def scan_hackerone():
    body = request.json or {}
    return jsonify(scan_hackerone_programs(
        limit=int(body.get("limit", 50)),
        force_refresh=bool(body.get("force_refresh", False)),
    ))


@app.route("/api/pipeline/cycle", methods=["POST"])
def pipeline_cycle():
    return jsonify(run_full_cycle())


@app.route("/api/patches")
def patches():
    from sentinel_earn.pipeline import list_patch_queue
    return jsonify({"patches": list_patch_queue()})


@app.route("/api/patches/<int:opp_id>/approve", methods=["POST"])
def approve_patch(opp_id: int):
    return jsonify(approve_opportunity(opp_id))


@app.route("/api/patches/<int:opp_id>/generate", methods=["POST"])
def generate_patch(opp_id: int):
    dry = bool((request.json or {}).get("dry_run", False))
    return jsonify(generate_patch_for_opportunity(opp_id, dry_run=dry) or {})


@app.route("/api/patches/<int:opp_id>/submit", methods=["POST"])
def submit(opp_id: int):
    return jsonify(submit_patch(opp_id))


@app.route("/api/submissions")
def submissions():
    return jsonify({"submissions": db.list_submissions()})


@app.route("/api/earnings")
def earnings():
    return jsonify(db.get_earnings_summary())


@app.route("/api/settings", methods=["GET", "POST"])
def settings():
    if request.method == "GET":
        s = load_settings()
        safe = {**s, "github_token": "***" if s.get("github_token") else "", "hackerone_api_token": "***" if s.get("hackerone_api_token") else ""}
        return jsonify(safe)
    body = request.json or {}
    current = load_settings()
    for key in ("ollama_host", "ollama_model", "github_token", "github_username", "hackerone_username", "hackerone_api_token", "scan_interval_minutes", "auto_generate_patches"):
        if key in body:
            val = body[key]
            if val == "***":
                continue
            current[key] = val
    save_settings(current)
    apply_settings()
    return jsonify({"status": "ok"})


@app.route("/api/ollama/status")
def ollama_status():
    import httpx
    host = load_settings().get("ollama_host", "http://127.0.0.1:11434").rstrip("/")
    try:
        r = httpx.get(f"{host}/api/tags", timeout=5)
        models = [m.get("name") for m in r.json().get("models", [])] if r.status_code == 200 else []
        return jsonify({"online": r.status_code == 200, "models": models, "host": host})
    except Exception as e:
        return jsonify({"online": False, "error": str(e), "host": host})


@app.route("/api/logs")
def logs():
    return jsonify({"logs": db.get_recent_logs(limit=100)})


def main():
    port = int(os.getenv("SENTINEL_EARN_PORT", "5120"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
