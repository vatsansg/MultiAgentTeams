"""Dashboard routes: the agent roster view, project/schedule management,
on-demand run triggering, and the JSON endpoints the page polls for live
status.
"""
import json
from pathlib import Path

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

import config
import db
import pipeline
import run_manager
import scheduler

bp = Blueprint("dashboard", __name__)

# The agent roster shown on the dashboard is NOT a separate list maintained
# here - it's read straight from pipeline.COORDINATOR / pipeline.SPECIALISTS,
# the same data agent creation itself loops over. Add a specialist there
# (see the comment above pipeline.SPECIALISTS for the steps) and it appears
# in the org chart below with no dashboard.py or template changes needed.


def _agent_cache_status():
    path = Path(config.AGENT_CACHE_PATH)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


@bp.route("/")
def index():
    projects = db.list_projects()
    runs = db.list_runs(limit=30)
    agent_ids = _agent_cache_status()
    return render_template(
        "dashboard.html",
        coordinator=pipeline.COORDINATOR,
        specialists=pipeline.SPECIALISTS,
        agent_ids=agent_ids,
        projects=projects,
        runs=runs,
        session_count=len(db.list_all_session_ids()),
    )


@bp.route("/projects", methods=["POST"])
def create_project():
    name = request.form.get("name", "").strip()
    brief = request.form.get("brief", "").strip()
    cron_expression = request.form.get("cron_expression", "").strip()
    interval_minutes = request.form.get("interval_minutes", "").strip()

    if not name or not brief:
        return redirect(url_for("dashboard.index"))

    project_id = db.create_project(
        name, brief, cron_expression, int(interval_minutes) if interval_minutes else None,
    )
    if cron_expression or interval_minutes:
        scheduler.add_or_update_job(
            project_id, cron_expression, int(interval_minutes) if interval_minutes else None,
        )
    return redirect(url_for("dashboard.index"))


@bp.route("/projects/<int:project_id>/run", methods=["POST"])
def run_project(project_id):
    run_manager.start_run(project_id, trigger_type="manual")
    return redirect(url_for("dashboard.index"))


@bp.route("/projects/<int:project_id>/schedule", methods=["POST"])
def update_schedule(project_id):
    cron_expression = request.form.get("cron_expression", "").strip()
    interval_minutes = request.form.get("interval_minutes", "").strip()
    enabled = bool(cron_expression or interval_minutes)

    db.update_schedule(
        project_id, cron_expression, int(interval_minutes) if interval_minutes else None, enabled,
    )
    if enabled:
        scheduler.add_or_update_job(
            project_id, cron_expression, int(interval_minutes) if interval_minutes else None,
        )
    else:
        scheduler.remove_job(project_id)
    return redirect(url_for("dashboard.index"))


@bp.route("/projects/<int:project_id>/delete", methods=["POST"])
def delete_project(project_id):
    scheduler.remove_job(project_id)
    db.delete_project(project_id)
    return redirect(url_for("dashboard.index"))


@bp.route("/teardown", methods=["POST"])
def teardown():
    """Deletes/archives every Managed Agents platform resource this app has
    created (sessions, the environment, memory stores, and - as far as the
    platform allows, see pipeline.teardown_platform_resources - the agents),
    so nothing keeps costing anything after you're done. Requires the
    confirmation modal's checkbox state for whether to also delete the
    Google Docs/Slack vaults, which this app doesn't own.
    """
    if db.has_running_run():
        flash("A run is still in progress - stop it first, then tear down platform resources.", "error")
        return redirect(url_for("dashboard.index"))

    if not config.ANTHROPIC_API_KEY:
        flash("ANTHROPIC_API_KEY is not configured - nothing to tear down yet.", "error")
        return redirect(url_for("dashboard.index"))

    include_vaults = request.form.get("include_vaults") == "on"
    session_ids = db.list_all_session_ids()

    from anthropic import Anthropic  # imported lazily, same pattern as pipeline.py

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    result = pipeline.teardown_platform_resources(client, session_ids, include_vaults=include_vaults)
    pipeline.clear_agent_cache()

    if result.deleted:
        flash(
            f"Removed {len(result.deleted)} platform resource(s): " + "; ".join(result.deleted) + ". "
            "The next run creates fresh agents, environment, and memory stores.",
            "success",
        )
    if not result.deleted and not result.failed:
        flash("Nothing was cached to remove - there was no platform footprint to tear down.", "success")
    if result.failed:
        flash(
            f"{len(result.failed)} resource(s) failed to delete: " + "; ".join(result.failed),
            "error",
        )

    return redirect(url_for("dashboard.index"))


@bp.route("/runs/<int:run_id>/stop", methods=["POST"])
def stop_run(run_id):
    """Best-effort stop for a run stuck in (or just no longer wanted while)
    'running' - see run_manager.request_stop for exactly what this does and
    does not guarantee."""
    run_manager.request_stop(run_id)
    return redirect(url_for("dashboard.index"))


@bp.route("/runs/<int:run_id>/delete", methods=["POST"])
def delete_run(run_id):
    """Remove a finished run from history. Refuses to delete a run that's
    still 'running' - stop it first, so a live background thread never
    writes a result for a row that's already gone."""
    run = db.get_run(run_id)
    if run is not None and run["status"] != "running":
        db.delete_run(run_id)
    return redirect(url_for("dashboard.index"))


@bp.route("/runs/<int:run_id>/files/<file_type>")
def download_run_file(run_id, file_type):
    """Serves a run's saved BRD or Technical Design Document so the
    dashboard's BRD/TDD links actually open something, instead of the
    placeholder href="#" they used to be."""
    if file_type not in ("brd", "tdd"):
        abort(404)

    run = db.get_run(run_id)
    if run is None:
        abort(404)

    path_str = run["brd_path"] if file_type == "brd" else run["tdd_path"]
    if not path_str:
        abort(404)

    path = Path(path_str)
    # Defense in depth: only ever serve files pipeline.py itself downloaded,
    # which always land under config.OUTPUTS_DIR - never an arbitrary path
    # that might end up in the database some other way.
    try:
        path.resolve().relative_to(config.OUTPUTS_DIR.resolve())
    except ValueError:
        abort(404)
    if not path.exists():
        abort(404)

    return send_file(path, mimetype="text/markdown", as_attachment=False, download_name=path.name)


# --- JSON polling endpoints ------------------------------------------------


@bp.route("/api/runs")
def api_runs():
    runs = db.list_runs(limit=30)
    return jsonify([dict(r) for r in runs])


@bp.route("/api/runs/<int:run_id>/log")
def api_run_log(run_id):
    return jsonify(run_manager.get_run_log(run_id))


@bp.route("/api/runs/<int:run_id>/cost")
def api_run_cost(run_id):
    """Polled every 30s by the dashboard for any row still showing
    'running', to display cost updating live rather than only once the run
    finishes. cost_usd is null if there's nothing new to report yet."""
    return jsonify({"cost_usd": run_manager.get_live_cost(run_id)})
