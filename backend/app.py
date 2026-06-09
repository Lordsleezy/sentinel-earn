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
from sentinel_earn.setup_engine import get_setup_engine
from sentinel_earn import ollama_runtime
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
get_setup_engine().start_background()


@app.route("/api/ping")
def ping():
    port = int(os.getenv("SENTINEL_EARN_PORT", "5120"))
    return jsonify({"status": "ok", "app": "Sentinel Earn", "port": port})


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
        safe = {
            **s,
            "github_token": "***" if s.get("github_token") else "",
            "hackerone_api_token": "***" if s.get("hackerone_api_token") else "",
            "github_token_set": bool((s.get("github_token") or "").strip()),
        }
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


@app.route("/api/setup/status")
def setup_status():
    setup = get_setup_engine().snapshot()
    ready = ollama_runtime.load_ready()
    return jsonify({
        **setup,
        "setup_complete": bool(setup.get("complete") or ollama_runtime.is_setup_complete()),
        "ollama_installed": ollama_runtime.ollama_installed(),
        "ollama_running": ollama_runtime.ollama_running(),
        "ready_model": ready.get("model"),
    })


@app.route("/api/setup/retry", methods=["POST"])
def setup_retry():
    get_setup_engine().retry()
    return jsonify({"status": "started"})


@app.route("/api/health")
def health():
    setup = get_setup_engine().snapshot()
    host = load_settings().get("ollama_host", "http://127.0.0.1:11434").rstrip("/")
    ollama_online = ollama_runtime.ollama_running()
    models = list(ollama_runtime.list_installed_models().keys()) if ollama_online else []
    return jsonify({
        "backend": "online",
        "setup_complete": bool(setup.get("complete") or ollama_runtime.is_setup_complete()),
        "setup": setup,
        "ollama": {
            "online": ollama_online,
            "installed": ollama_runtime.ollama_installed(),
            "host": host,
            "models": models,
        },
    })


@app.route("/api/ollama/status")
def ollama_status():
    host = load_settings().get("ollama_host", "http://127.0.0.1:11434").rstrip("/")
    online = ollama_runtime.ollama_running()
    models = list(ollama_runtime.list_installed_models().keys()) if online else []
    return jsonify({"online": online, "models": models, "host": host})


@app.route("/api/logs")
def logs():
    return jsonify({"logs": db.get_recent_logs(limit=100)})


def main():
    port = int(os.getenv("SENTINEL_EARN_PORT", "5120"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
