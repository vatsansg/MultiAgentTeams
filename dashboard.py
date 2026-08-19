"""Dashboard routes: agent catalog, team management, project/schedule
management, on-demand run triggering, and the JSON endpoints the page
polls for live status.
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


def _agent_cache_status():
    path = Path(config.AGENT_CACHE_PATH)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _spec_view(row):
    """agent_specs/team_members row -> plain dict with `skills` parsed from
    its stored JSON string into a list, for template rendering."""
    d = dict(row)
    try:
        d["skills"] = json.loads(d["skills"] or "[]")
    except json.JSONDecodeError:
        d["skills"] = []
    return d


# --- dashboard --------------------------------------------------------


@bp.route("/")
def index():
    teams = db.list_teams()
    selected_team_id = request.args.get("team_id", type=int)
    if selected_team_id is None and teams:
        selected_team_id = teams[0]["id"]
    selected_team = db.get_team_with_members(selected_team_id) if selected_team_id else None

    return render_template(
        "dashboard.html",
        teams=teams,
        selected_team=selected_team,
        selected_team_id=selected_team_id,
        all_team_members=[_spec_view(m) for m in db.list_all_team_members()],
        agent_ids=_agent_cache_status(),
        projects=db.list_projects(),
        runs=db.list_runs(limit=30),
        session_count=len(db.list_all_session_ids()),
    )


@bp.route("/projects", methods=["POST"])
def create_project():
    name = request.form.get("name", "").strip()
    brief = request.form.get("brief", "").strip()
    team_id = request.form.get("team_id", type=int)

    if not name or not brief:
        flash("Project name and brief are required.", "error")
        return redirect(url_for("dashboard.index"))
    if not team_id or db.get_team(team_id) is None:
        flash("Pick a team before creating a project.", "error")
        return redirect(url_for("dashboard.index"))

    schedule, error = scheduler.parse_schedule_form(request.form)
    if error:
        flash(error, "error")
        return redirect(url_for("dashboard.index", team_id=team_id))

    project_id = db.create_project(name, brief, team_id, schedule)
    if schedule:
        scheduler.add_or_update_job(project_id, schedule)
    return redirect(url_for("dashboard.index", team_id=team_id))


@bp.route("/projects/<int:project_id>/run", methods=["POST"])
def run_project(project_id):
    run_manager.start_run(project_id, trigger_type="manual")
    return redirect(url_for("dashboard.index"))


@bp.route("/projects/<int:project_id>/schedule", methods=["POST"])
def update_schedule(project_id):
    schedule, error = scheduler.parse_schedule_form(request.form)
    if error:
        flash(error, "error")
        return redirect(url_for("dashboard.index"))

    db.update_schedule(project_id, schedule or {})
    if schedule:
        scheduler.add_or_update_job(project_id, schedule)
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
    platform allows, see pipeline.teardown_platform_resources - every
    team's agents), so nothing keeps costing anything after you're done.
    Requires the confirmation modal's checkbox state for whether to also
    delete the Google Docs/Slack vaults, which this app doesn't own.
    """
    if db.has_running_run():
        flash("A run is still in progress - stop it first, then tear down platform resources.", "error")
        return redirect(url_for("dashboard.index"))

    if not config.ANTHROPIC_API_KEY:
        flash("ANTHROPIC_API_KEY is not configured - nothing to tear down yet.", "error")
        return redirect(url_for("dashboard.index"))

    include_vaults = request.form.get("include_vaults") == "on"
    session_ids = db.list_all_session_ids()
    agent_roster = [
        {
            "cache_key": m["cache_key"],
            "label": f"{m['display_name']} ({m['role_title']}, team: {m['team_name']})",
        }
        for m in db.list_all_team_members()
    ]

    from anthropic import Anthropic  # imported lazily, same pattern as pipeline.py

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    result = pipeline.teardown_platform_resources(
        client, session_ids, agent_roster, include_vaults=include_vaults,
    )
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
    """Serves a run's saved BRD, Technical Design Document, or website
    zip so the dashboard's Files links actually open/download something,
    instead of a placeholder href="#"."""
    if file_type not in ("brd", "tdd", "site"):
        abort(404)

    run = db.get_run(run_id)
    if run is None:
        abort(404)

    path_str = {"brd": run["brd_path"], "tdd": run["tdd_path"], "site": run["site_path"]}[file_type]
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

    if file_type == "site":
        return send_file(
            path, mimetype="application/zip", as_attachment=True,
            download_name=f"{run['project_name']}-site.zip",
        )
    return send_file(path, mimetype="text/markdown", as_attachment=False, download_name=path.name)


# --- agents catalog (read-only) --------------------------------------------


@bp.route("/agents")
def agents():
    return render_template("agents.html", specs=[_spec_view(s) for s in db.list_agent_specs()])


# --- teams -------------------------------------------------------------


@bp.route("/teams")
def teams():
    team_views = [
        {
            "team": t,
            "members": [_spec_view(m) for m in db.list_team_members(t["id"])],
            "in_use": db.team_in_use(t["id"]),
        }
        for t in db.list_teams()
    ]
    return render_template("teams.html", team_views=team_views)


@bp.route("/teams/new", methods=["GET", "POST"])
def new_team():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        spec_ids = [int(v) for v in request.form.getlist("spec_ids") if v.isdigit()]
        if not name:
            flash("Team name is required.", "error")
            return redirect(url_for("dashboard.new_team"))
        specs = db.list_agent_specs()
        if not specs:
            flash("No agent specs exist yet - cannot create a team.", "error")
            return redirect(url_for("dashboard.new_team"))
        try:
            team_id = db.create_team(name, description, spec_ids)
        except db.DuplicateNameError:
            flash(f'A team named "{name}" already exists.', "error")
            return redirect(url_for("dashboard.new_team"))
        member_count = len(db.list_team_members(team_id))
        flash(f'Team "{name}" created with {member_count} agent(s).', "success")
        return redirect(url_for("dashboard.teams"))

    return render_template("team_new.html", specs=[_spec_view(s) for s in db.list_agent_specs()])


@bp.route("/teams/<int:team_id>/edit", methods=["GET", "POST"])
def edit_team(team_id):
    team = db.get_team(team_id)
    if team is None:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        spec_ids = [int(v) for v in request.form.getlist("spec_ids") if v.isdigit()]
        if not name:
            flash("Team name is required.", "error")
            return redirect(url_for("dashboard.edit_team", team_id=team_id))

        try:
            db.rename_team(team_id, name, description)
        except db.DuplicateNameError:
            flash(f'A team named "{name}" already exists.', "error")
            return redirect(url_for("dashboard.edit_team", team_id=team_id))

        changed = db.update_team_members(team_id, spec_ids)
        if changed:
            coordinator_cache_key = db.get_team_coordinator_cache_key(team_id)
            if coordinator_cache_key:
                pipeline.invalidate_cached_agent(coordinator_cache_key)
            flash(
                "Team membership updated. The coordinator will be recreated on the platform "
                "the next time this team runs, to pick up the new roster.", "success",
            )
        else:
            flash("Team updated.", "success")
        return redirect(url_for("dashboard.teams"))

    member_spec_ids = {
        m["agent_spec_id"] for m in db.list_team_members(team_id) if m["agent_spec_id"] is not None
    }
    return render_template(
        "team_edit.html", team=team,
        specs=[_spec_view(s) for s in db.list_agent_specs()],
        member_spec_ids=member_spec_ids,
    )


@bp.route("/teams/<int:team_id>/delete", methods=["POST"])
def delete_team(team_id):
    if db.team_in_use(team_id):
        flash(
            "This team is still assigned to at least one project - reassign or delete those "
            "projects first.", "error",
        )
        return redirect(url_for("dashboard.teams"))
    db.delete_team(team_id)
    flash("Team deleted.", "success")
    return redirect(url_for("dashboard.teams"))


# --- agent specs (edit + rule-based validation) -----------------------------


_WORD_LIMIT = 10000


def _validate_agent_spec(spec, fields) -> list:
    """Rule-based (no LLM call - fast, free, deterministic) sanity checks
    for an edited agent spec."""
    issues = []
    if not fields["display_name"]:
        issues.append("Display name is required.")
    if not fields["role_title"]:
        issues.append("Role title is required.")
    if not fields["description"]:
        issues.append("Description is required.")
    if not fields["system_prompt"]:
        issues.append("System prompt is required.")
    elif len(fields["system_prompt"].split()) > _WORD_LIMIT:
        issues.append(f"System prompt is over the {_WORD_LIMIT:,}-word soft limit.")
    if not spec["is_coordinator"] and not fields["handoff_instructions"]:
        issues.append(
            "Non-coordinator roles need handoff instructions (used to build the coordinator's "
            "delegation step)."
        )
    if spec["is_coordinator"]:
        for token in ("{{DELEGATION_STEPS}}", "{{CLOSING_STEPS}}"):
            if token not in fields["system_prompt"]:
                issues.append(
                    f"Coordinator system prompt is missing the {token} placeholder - dynamic "
                    "steps won't be inserted."
                )
    elif "/workspace/" not in fields["system_prompt"]:
        issues.append("Non-coordinator system prompt does not mention a /workspace/ output path.")
    try:
        skills = json.loads(fields["skills"] or "[]")
        if not isinstance(skills, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        issues.append('Skills must be a valid JSON list, e.g. ["Node.js", "React"].')
    if not fields["tools_label"]:
        issues.append("Tools label is required.")
    return issues


@bp.route("/agent-specs")
def agent_specs():
    return render_template("agent_specs.html", specs=[_spec_view(s) for s in db.list_agent_specs()])


@bp.route("/agent-specs/<int:spec_id>/edit", methods=["GET", "POST"])
def edit_agent_spec(spec_id):
    spec = db.get_agent_spec(spec_id)
    if spec is None:
        abort(404)

    if request.method == "POST":
        fields = {
            k: request.form.get(k, "").strip() for k in
            ("display_name", "role_title", "description", "system_prompt",
             "handoff_instructions", "skills", "tools_label")
        }
        issues = _validate_agent_spec(spec, fields)
        action = request.form.get("action", "save")

        if action == "validate" or issues:
            for msg in issues:
                flash(msg, "error")
            if action == "validate" and not issues:
                flash("Validation passed - no issues found.", "success")
            return render_template("agent_spec_edit.html", spec=spec, form=fields, issues=issues)

        db.update_agent_spec(spec_id, **fields)
        flash(
            f"{fields['display_name']} updated. Existing teams keep their already-provisioned "
            "agent - only teams created after this change pick up the new prompt.", "success",
        )
        return redirect(url_for("dashboard.agent_specs"))

    form = {
        k: (spec[k] or "") for k in
        ("display_name", "role_title", "description", "system_prompt",
         "handoff_instructions", "skills", "tools_label")
    }
    return render_template("agent_spec_edit.html", spec=spec, form=form, issues=[])


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
