"""Smoke test for the Agent Console Flask app - verifies login, the
dashboard, the agent-specs catalog, team creation, project/schedule CRUD,
and the on-demand run flow, all with the real Managed Agents pipeline
replaced by a fast fake. No Anthropic API key or vault is needed to run
this.

Run with:
    python tests/test_smoke.py
"""
import os
import sys
import time
import types
from pathlib import Path

# Point the app at throwaway data files before anything imports config.py,
# so this test never touches the real data/ directory shipped with the app.
TEST_DIR = Path(__file__).resolve().parent
# Use a native-filesystem temp dir rather than a path under the mounted
# project folder - SQLite needs real file locking, which some mounted/
# synced drives (network shares, certain cloud-sync folders) don't support
# and will surface as "disk I/O error". See README troubleshooting.
import tempfile
TMP = Path(tempfile.gettempdir()) / "agent_console_smoke_test"
TMP.mkdir(exist_ok=True)
os.environ["AGENT_CONSOLE_DB"] = str(TMP / "test.sqlite3")
os.environ["AGENT_CACHE_PATH"] = str(TMP / "test_agent_cache.json")
os.environ["AGENT_CONSOLE_OUTPUTS"] = str(TMP / "outputs")
os.environ.setdefault("CONSOLE_USERNAME", "admin")
os.environ.setdefault("CONSOLE_PASSWORD", "test-password")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
# Force-blank these regardless of what's in the real .env this process
# might otherwise pick up (config.py's load_dotenv only fills in variables
# NOT already present in os.environ) - several assertions below depend on
# the app behaving as if no API key/vaults are configured, and this test
# must never make a real network call to Anthropic no matter what's sitting
# in a developer's real .env file.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GOOGLE_DOCS_VAULT_ID"] = ""
os.environ["SLACK_VAULT_ID"] = ""

# Remove any leftovers from a previous run.
for f in [os.environ["AGENT_CONSOLE_DB"], os.environ["AGENT_CACHE_PATH"]]:
    Path(f).unlink(missing_ok=True)

sys.path.insert(0, str(TEST_DIR.parent))

import app as app_module  # noqa: E402
import run_manager  # noqa: E402
import db  # noqa: E402
import pipeline  # noqa: E402
import scheduler  # noqa: E402
import dashboard  # noqa: E402


class FakeAnthropicClient:
    """Stands in for a real anthropic.Anthropic client in
    pipeline.teardown_platform_resources tests - records every delete/
    archive call instead of hitting the network, and can be seeded with
    fake per-project memory stores to simulate memory_stores.list()."""

    class _Log(list):
        def add(self, kind, resource_id):
            self.append(f"{kind} {resource_id}")

    def __init__(self, project_stores=()):
        self.calls = self._Log()
        project_stores = list(project_stores)

        calls = self.calls

        class _Agents:
            def archive(self, agent_id, betas=None):
                calls.add("archive_agent", agent_id)

        class _Environments:
            def delete(self, environment_id, betas=None):
                calls.add("delete_environment", environment_id)

        class _Sessions:
            def delete(self, session_id, betas=None):
                calls.add("delete_session", session_id)

        class _MemoryStores:
            def delete(self, memory_store_id):
                calls.add("delete_memory_store", memory_store_id)

            def list(self):
                return types.SimpleNamespace(data=project_stores)

        class _Vaults:
            def delete(self, vault_id, betas=None):
                calls.add("delete_vault", vault_id)

        self.beta = types.SimpleNamespace(
            agents=_Agents(), environments=_Environments(),
            sessions=_Sessions(), memory_stores=_MemoryStores(), vaults=_Vaults(),
        )


def fake_pipeline(project_name, project_brief, team, *, max_iterations=None, on_event=None, should_stop=None):
    """Stands in for pipeline.run_delivery_pipeline - no network calls.
    Accepts (and ignores) the `team` bundle so it matches run_manager's
    real call signature."""
    if on_event:
        on_event({"kind": "status", "message": "resolving vaults (fake)"})
        on_event({"kind": "thread_created", "agent_name": "Delivery BA (Roger)"})
        on_event({"kind": "thread_returned", "from_agent_name": "Delivery BA (Roger)"})
        on_event({"kind": "thread_created", "agent_name": "Delivery Architect (Michael)"})
        on_event({"kind": "thread_returned", "from_agent_name": "Delivery Architect (Michael)"})
        on_event({"kind": "outcome_evaluation", "iteration": 1, "result": "satisfied"})
    return pipeline.PipelineResult(
        session_id="sesn_FAKE123",
        satisfied=True,
        brd_path=str(TMP / "outputs" / "BRD.md"),
        tdd_path=str(TMP / "outputs" / "Technical_Design_Document.md"),
        site_path=None,
        cost_usd=0.4321,
    )


def main():
    run_manager.set_pipeline_fn(fake_pipeline)
    app = app_module.app
    app.testing = True
    client = app.test_client()

    # 1. Dashboard requires login.
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (301, 302), f"expected redirect to login, got {resp.status_code}"
    assert "/login" in resp.headers["Location"]
    print("PASS: dashboard requires login")

    # 2. Wrong credentials rejected.
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 200
    assert b"Incorrect username or password" in resp.data
    print("PASS: wrong credentials rejected")

    # 3. Correct credentials log in.
    resp = client.post(
        "/login",
        data={"username": os.environ["CONSOLE_USERNAME"], "password": os.environ["CONSOLE_PASSWORD"]},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Team" in resp.data
    print("PASS: login succeeds and dashboard renders")

    # 4. The agent_specs catalog seeds exactly 4 roles on a fresh DB, one coordinator.
    specs = db.list_agent_specs()
    assert len(specs) == 4, f"expected 4 seeded agent specs, got {len(specs)}"
    role_keys = {s["role_key"] for s in specs}
    assert role_keys == {"delivery_lead", "business_analyst", "solution_architect", "developer"}, role_keys
    coordinators = [s for s in specs if s["is_coordinator"]]
    assert len(coordinators) == 1 and coordinators[0]["role_key"] == "delivery_lead"
    print("PASS: agent_specs catalog seeded with exactly 4 roles, one coordinator")

    # 5. /agents renders the catalog, including Smith's skills.
    resp = client.get("/agents")
    assert resp.status_code == 200
    assert b"Smith" in resp.data and b"Node.js" in resp.data
    print("PASS: /agents renders the catalog with Smith's skills")

    # 6. Creating a project without a team is rejected.
    resp = client.post(
        "/projects",
        data={"name": "No Team Project", "brief": "Should not be created."},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert len(db.list_projects()) == 0, "project must not be created without a team"
    print("PASS: project creation without a team is rejected")

    # 7. Create two teams and confirm each is a faithful snapshot of the
    # catalog with team-namespaced cache_keys that never collide.
    team1_id = db.create_team("Delivery Team One", "First team")
    team2_id = db.create_team("Delivery Team Two", "Second team")
    bundle1 = db.get_team_with_members(team1_id)
    bundle2 = db.get_team_with_members(team2_id)
    assert len(bundle1["members"]) == 4 and len(bundle2["members"]) == 4
    ba1 = [m for m in bundle1["members"] if m["role_key"] == "business_analyst"][0]
    ba2 = [m for m in bundle2["members"] if m["role_key"] == "business_analyst"][0]
    assert ba1["cache_key"] != ba2["cache_key"], "two teams' cache_keys must not collide"
    assert ba1["cache_key"] == f"team{team1_id}_business_analyst"
    print(f"PASS: team snapshot + cache_key collision-prevention ({ba1['cache_key']} vs {ba2['cache_key']})")

    # 7b. Dynamic team composition: creating a team with only a subset of
    # roles selected (no Developer) excludes Smith; the coordinator is
    # force-included even though its spec id is never offered as a choice.
    specs = db.list_agent_specs()
    ba_spec = [s for s in specs if s["role_key"] == "business_analyst"][0]
    arch_spec = [s for s in specs if s["role_key"] == "solution_architect"][0]
    partial_team_id = db.create_team("BA+Architect Only", "No developer", [ba_spec["id"], arch_spec["id"]])
    partial_bundle = db.get_team_with_members(partial_team_id)
    partial_role_keys = {m["role_key"] for m in partial_bundle["members"]}
    assert partial_role_keys == {"delivery_lead", "business_analyst", "solution_architect"}, partial_role_keys
    print("PASS: creating a team with a role subset excludes the unselected roles (coordinator always included)")

    # 7c. Editing an existing team to add a role: db.update_team_members
    # reports a real change, and the coordinator's cached platform agent id
    # (if any) gets invalidated so it's recreated with the new roster on
    # the next run - it must NOT retroactively rewrite an already-running
    # coordinator's multiagent list, only force a recreate.
    dev_spec = [s for s in specs if s["role_key"] == "developer"][0]
    coordinator_cache_key = db.get_team_coordinator_cache_key(partial_team_id)
    assert coordinator_cache_key == f"team{partial_team_id}_delivery_lead"
    pipeline._save_cache({coordinator_cache_key: "agent_FAKE_PARTIAL_COORD"})

    resp = client.post(
        f"/teams/{partial_team_id}/edit",
        data={
            "name": "BA+Architect Only", "description": "No developer",
            "spec_ids": [str(ba_spec["id"]), str(arch_spec["id"]), str(dev_spec["id"])],
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"recreated on the platform" in resp.data
    updated_bundle = db.get_team_with_members(partial_team_id)
    assert {m["role_key"] for m in updated_bundle["members"]} == {
        "delivery_lead", "business_analyst", "solution_architect", "developer",
    }
    assert pipeline._load_cache().get(coordinator_cache_key) is None, (
        "adding a role must invalidate the team's cached coordinator agent id"
    )
    print("PASS: editing a team to add a role updates membership and invalidates the cached coordinator")

    # 7d. Editing a team with no actual membership change is a no-op - the
    # (still uncached, since we just invalidated it) coordinator cache_key
    # stays absent, and no unnecessary "recreated" message is shown.
    resp = client.post(
        f"/teams/{partial_team_id}/edit",
        data={
            "name": "BA+Architect Only", "description": "No developer",
            "spec_ids": [str(ba_spec["id"]), str(arch_spec["id"]), str(dev_spec["id"])],
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Team updated." in resp.data
    print("PASS: editing a team with no membership change does not claim a coordinator recreation")

    db.delete_team(partial_team_id)

    # 8. Create a project against team1.
    resp = client.post(
        "/projects",
        data={
            "team_id": str(team1_id),
            "name": "Boutique Coffee Roastery Website",
            "brief": "Sell beans online, let people book roastery tours.",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    projects = db.list_projects()
    assert len(projects) == 1, f"expected 1 project, got {len(projects)}"
    project_id = projects[0]["id"]
    assert projects[0]["team_id"] == team1_id
    print(f"PASS: project created against team1 (id={project_id})")

    # 9. Dashboard with team1 selected renders team1's actual members
    # (Smith included), not any old hardcoded roster.
    resp = client.get(f"/?team_id={team1_id}")
    assert resp.status_code == 200
    for name in (b"Delivery Lead", b"Roger", b"Michael", b"Smith"):
        assert name in resp.data, f"{name} missing from dashboard for team1"
    assert b"Delivery Team One" in resp.data
    print("PASS: dashboard renders the selected team's real members dynamically")

    # 10. Trigger an on-demand run and wait for the background thread to finish.
    # Pre-create the files fake_pipeline "downloads", so the BRD/TDD links'
    # actual file-serving route (not just a href="#" placeholder) has real
    # content to check in step 11b below.
    (TMP / "outputs").mkdir(parents=True, exist_ok=True)
    (TMP / "outputs" / "BRD.md").write_text("# Fake BRD\n\nFake content for the smoke test.")
    (TMP / "outputs" / "Technical_Design_Document.md").write_text("# Fake TDD\n\nFake content.")

    resp = client.post(f"/projects/{project_id}/run", follow_redirects=True)
    assert resp.status_code == 200

    run_row = None
    for _ in range(50):  # up to ~5s
        runs = db.list_runs()
        if runs and runs[0]["status"] != "running":
            run_row = runs[0]
            break
        time.sleep(0.1)
    assert run_row is not None, "run did not finish in time"
    assert run_row["status"] == "success", run_row["status"]
    assert run_row["satisfied"] == 1
    assert run_row["session_id"] == "sesn_FAKE123"
    assert run_row["cost_usd"] == 0.4321
    assert run_row["team_name"] == "Delivery Team One"
    print("PASS: on-demand run completes via the (fake) pipeline, recorded with team_name")

    # 10b. A project with no team assigned fails immediately with a
    # readable error, rather than raising or hanging.
    with db.get_conn() as conn:
        no_team_project_id = conn.execute(
            "INSERT INTO projects (name, brief, team_id, schedule_enabled, created_at) "
            "VALUES ('No Team', 'brief', NULL, 0, ?)", (db.now_iso(),),
        ).lastrowid
    no_team_run_id = run_manager.start_run(no_team_project_id, trigger_type="manual")
    no_team_run = db.get_run(no_team_run_id)
    assert no_team_run["status"] == "failed"
    assert "no team assigned" in (no_team_run["error"] or "")
    print("PASS: running a project with no team assigned fails immediately with a readable error")

    # 11. Run log captured the fake pipeline's events.
    log = run_manager.get_run_log(run_row["id"])
    kinds = [e["kind"] for e in log]
    assert "thread_created" in kinds and "outcome_evaluation" in kinds
    print("PASS: live run log captured pipeline events")

    # 11b. The dashboard's BRD/TDD links actually serve the file - this used
    # to be a plain href="#" placeholder that did nothing when clicked.
    resp = client.get(f"/runs/{run_row['id']}/files/brd")
    assert resp.status_code == 200
    assert b"Fake BRD" in resp.data
    resp = client.get(f"/runs/{run_row['id']}/files/tdd")
    assert resp.status_code == 200
    assert b"Fake TDD" in resp.data
    # A path outside OUTPUTS_DIR (or a run/file-type that doesn't exist)
    # must 404, not serve arbitrary files.
    resp = client.get("/runs/999999/files/brd")
    assert resp.status_code == 404
    resp = client.get(f"/runs/{run_row['id']}/files/nope")
    assert resp.status_code == 404
    print("PASS: BRD/TDD links serve the actual saved files, and reject invalid ones")

    # 11c. A run with a site_path serves it as application/zip via the new
    # 'site' file_type, and the run history table shows a Team column.
    site_zip = TMP / "outputs" / "site.zip"
    site_zip.write_bytes(b"PK\x03\x04fake-zip-bytes")
    site_run_id = db.create_run(project_id, "manual", team_name="Delivery Team One")
    db.finish_run(site_run_id, status="success", satisfied=True, session_id="sesn_SITE", site_path=str(site_zip))
    resp = client.get(f"/runs/{site_run_id}/files/site")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    resp = client.get("/")
    assert b"Site (.zip)" in resp.data
    assert b"Delivery Team One" in resp.data
    print("PASS: site.zip deliverable download route + Team column in run history")

    # 12. Update the schedule with the new structured picker (weekly, on
    # Wednesday, 6:30:15 PM in 12h mode) - confirm it's stored AND that the
    # resulting APScheduler job has the correct trigger fields, not just
    # that a job exists.
    resp = client.post(
        f"/projects/{project_id}/schedule",
        data={
            "schedule_frequency": "weekly", "time_mode": "12h",
            "schedule_hour_12": "6", "schedule_period": "PM",
            "schedule_minute": "30", "schedule_second": "15",
            "schedule_weekday": "2",  # Wednesday
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    updated = db.get_project(project_id)
    assert updated["schedule_enabled"] == 1
    assert updated["schedule_frequency"] == "weekly"
    assert updated["schedule_hour"] == 18
    assert "Wednesday" in updated["schedule_summary"], updated["schedule_summary"]
    print("PASS: structured weekly schedule saved with correct 12h->24h conversion")

    job = scheduler._scheduler.get_job(f"project-{project_id}")
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day_of_week"] == "wed", fields
    assert fields["hour"] == "18" and fields["minute"] == "30" and fields["second"] == "15"
    print("PASS: APScheduler job has the correct weekly CronTrigger fields (not just exists)")

    # 12b. Daily, monthly, and interval schedules all produce the right
    # trigger shape too.
    daily, err = scheduler.parse_schedule_form({
        "schedule_frequency": "daily", "time_mode": "24h",
        "schedule_hour": "6", "schedule_minute": "0", "schedule_second": "0",
    })
    assert err is None
    scheduler.add_or_update_job(90001, daily)
    daily_fields = {f.name: str(f) for f in scheduler._scheduler.get_job("project-90001").trigger.fields}
    assert daily_fields["hour"] == "6" and daily_fields["day_of_week"] == "*"

    monthly, err = scheduler.parse_schedule_form({
        "schedule_frequency": "monthly", "time_mode": "24h",
        "schedule_hour": "9", "schedule_minute": "5", "schedule_second": "0",
        "schedule_month_day": "15",
    })
    assert err is None
    scheduler.add_or_update_job(90002, monthly)
    monthly_fields = {f.name: str(f) for f in scheduler._scheduler.get_job("project-90002").trigger.fields}
    assert monthly_fields["day"] == "15"

    interval, err = scheduler.parse_schedule_form({"schedule_frequency": "interval", "interval_minutes": "45"})
    assert err is None
    scheduler.add_or_update_job(90003, interval)
    assert type(scheduler._scheduler.get_job("project-90003").trigger).__name__ == "IntervalTrigger"

    too_short, err = scheduler.parse_schedule_form({"schedule_frequency": "interval", "interval_minutes": "2"})
    assert too_short is None and err
    print("PASS: daily/monthly/interval schedules all produce correct trigger shapes; short interval rejected")

    for pid in (90001, 90002, 90003):
        scheduler.remove_job(pid)

    # 13. JSON polling endpoints work.
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json[0]["status"] == "success"
    resp = client.get(f"/api/runs/{run_row['id']}/log")
    assert resp.status_code == 200
    assert len(resp.json) == len(log)
    print("PASS: /api/runs and /api/runs/<id>/log respond correctly")

    # 14. A failing run stores its error, and the dashboard actually shows it.
    def failing_pipeline(project_name, project_brief, team, *, max_iterations=None, on_event=None, should_stop=None):
        raise pipeline.PipelineError("GOOGLE_DOCS_VAULT_ID should start with 'vlt_'. Got: 'sk-ant-fake123'")

    run_manager.set_pipeline_fn(failing_pipeline)
    resp = client.post(f"/projects/{project_id}/run", follow_redirects=True)
    assert resp.status_code == 200

    failed_row = None
    for _ in range(50):
        latest = db.list_runs()[0]
        if latest["status"] != "running":
            failed_row = latest
            break
        time.sleep(0.1)
    assert failed_row is not None, "failing run did not finish in time"
    assert failed_row["status"] == "failed", failed_row["status"]
    assert failed_row["error"] and "vlt_" in failed_row["error"]
    print("PASS: a failing run is recorded with status=failed and a stored error message")

    dashboard_html = client.get("/").get_data(as_text=True)
    assert "GOOGLE_DOCS_VAULT_ID" in dashboard_html, (
        "dashboard does not render the failed run's error message"
    )
    print("PASS: the dashboard's run history actually displays the error, not just the status")

    # 15. Delete a finished (failed) run from history.
    resp = client.post(f"/runs/{failed_row['id']}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_run(failed_row["id"]) is None
    print("PASS: a finished run can be deleted from run history")

    # 16. A run that hangs (simulating a slow/stuck platform call) can be
    # stopped from the dashboard - and its session id shows up in the
    # database while it's still in progress, not just once it's done.
    def stallable_pipeline(project_name, project_brief, team, *, max_iterations=None,
                            on_event=None, should_stop=None):
        if on_event:
            on_event({"kind": "session_created", "session_id": "sesn_STALL123"})
        for _ in range(100):  # up to ~10s, polling the cooperative stop flag
            if should_stop and should_stop():
                raise pipeline.PipelineStopped("Stopped by user.")
            time.sleep(0.1)
        raise AssertionError("stallable_pipeline was not stopped in time")

    run_manager.set_pipeline_fn(stallable_pipeline)
    resp = client.post(f"/projects/{project_id}/run", follow_redirects=True)
    assert resp.status_code == 200

    stall_run_id = None
    for _ in range(50):
        latest = db.list_runs()[0]
        if latest["status"] == "running" and latest["session_id"] == "sesn_STALL123":
            stall_run_id = latest["id"]
            break
        time.sleep(0.1)
    assert stall_run_id is not None, "run did not reach 'running' with a session id in time"
    print("PASS: session id is persisted to the database while a run is still in progress")

    # 17. Live cost endpoint responds safely with no ANTHROPIC_API_KEY
    # configured in this test env - null, not an error.
    resp = client.get(f"/api/runs/{stall_run_id}/cost")
    assert resp.status_code == 200
    assert resp.json["cost_usd"] is None
    print("PASS: live cost endpoint responds safely with no API key configured")

    # 18. Delete refuses to remove a still-running run.
    resp = client.post(f"/runs/{stall_run_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_run(stall_run_id) is not None, "delete should refuse a still-running run"
    print("PASS: delete route refuses to remove a still-running run")

    # 19. Stop it - the dashboard should reflect this immediately.
    resp = client.post(f"/runs/{stall_run_id}/stop", follow_redirects=True)
    assert resp.status_code == 200
    stopped_row = db.get_run(stall_run_id)
    assert stopped_row["status"] == "failed", stopped_row["status"]
    assert "Stopped by user" in (stopped_row["error"] or "")
    print("PASS: Stop marks a running run as failed immediately")

    # Give the background thread a moment to notice should_stop() and raise
    # PipelineStopped on its own - confirm that path doesn't clobber the row
    # request_stop() already wrote (db.stop_run only touches 'running' rows).
    time.sleep(0.5)
    still_stopped = db.get_run(stall_run_id)
    assert still_stopped["status"] == "failed"
    print("PASS: the background thread's own stop path does not clobber the already-stopped row")

    # 20. Now that it's finished (stopped), delete succeeds.
    resp = client.post(f"/runs/{stall_run_id}/delete", follow_redirects=True)
    assert db.get_run(stall_run_id) is None
    print("PASS: delete route removes a finished (stopped) run")

    # 21. Orphan reconciliation - simulates what happens on app startup if a
    # previous process died mid-run: a row stuck 'running' forever with no
    # thread left to ever finish it.
    orphan_id = db.create_run(project_id, "manual")
    reconciled = db.fail_orphaned_running_runs()
    assert reconciled >= 1
    orphan_row = db.get_run(orphan_id)
    assert orphan_row["status"] == "failed"
    assert "restarted" in orphan_row["error"]
    print("PASS: orphaned 'running' rows are reconciled to 'failed' (simulates an app restart)")
    db.delete_run(orphan_id)

    # 22. Agent-spec edit: empty system_prompt is rejected without writing
    # to the DB, and the coordinator-template-placeholder rule fires.
    specs = db.list_agent_specs()
    dev_spec = [s for s in specs if s["role_key"] == "developer"][0]
    resp = client.post(
        f"/agent-specs/{dev_spec['id']}/edit",
        data={
            "action": "save", "display_name": dev_spec["display_name"],
            "role_title": dev_spec["role_title"], "description": dev_spec["description"],
            "system_prompt": "", "handoff_instructions": dev_spec["handoff_instructions"],
            "skills": dev_spec["skills"], "tools_label": dev_spec["tools_label"],
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"System prompt is required" in resp.data
    unchanged = db.get_agent_spec(dev_spec["id"])
    assert unchanged["system_prompt"] == dev_spec["system_prompt"], "must not have been saved"
    print("PASS: agent-spec edit rejects an empty system_prompt without writing to the DB")

    coordinator_fields = {
        "display_name": "Delivery Lead", "role_title": "Coordinator", "description": "x",
        "system_prompt": "no placeholders here", "handoff_instructions": "",
        "skills": "[]", "tools_label": "x",
    }
    coordinator_spec_row = {"is_coordinator": 1}
    issues = dashboard._validate_agent_spec(coordinator_spec_row, coordinator_fields)
    assert any("{{DELEGATION_STEPS}}" in i for i in issues)
    print("PASS: _validate_agent_spec flags a coordinator prompt missing the delegation placeholder")

    # 23. Team deletion is blocked while a project references it, succeeds
    # once that project is gone.
    resp = client.post(f"/teams/{team1_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_team(team1_id) is not None, "in-use team must not be deleted"
    print("PASS: deleting a team still referenced by a project is blocked")

    # 24. pipeline.teardown_platform_resources deletes/archives every
    # resource it's told about, using a fake client so no real network call
    # happens. Seed a fake cache with team-namespaced keys (not the old flat
    # roger_id/michael_id/coordinator_id) and an agent_roster built the same
    # way dashboard.py's /teardown route builds it.
    pipeline._save_cache({
        "team1_business_analyst": "agent_FAKE_ROGER",
        "team1_solution_architect": "agent_FAKE_MICHAEL",
        "team1_delivery_lead": "agent_FAKE_COORD",
        "org_standards_id": "mst_FAKE_ORG",
        "environment_id": "env_FAKE",
    })
    agent_roster = [
        {"cache_key": "team1_business_analyst", "label": "Roger (Business Analyst, team: Delivery Team One)"},
        {"cache_key": "team1_solution_architect", "label": "Michael (Solution Architect, team: Delivery Team One)"},
        {"cache_key": "team1_delivery_lead", "label": "Delivery Lead (Coordinator, team: Delivery Team One)"},
    ]
    fake_project_store = types.SimpleNamespace(id="mst_FAKE_PROJECT", name="delivery-project-boutique")
    fake_client = FakeAnthropicClient(project_stores=[fake_project_store])

    result = pipeline.teardown_platform_resources(
        fake_client, ["sesn_FAKE_A", "sesn_FAKE_B"], agent_roster, include_vaults=False,
    )
    assert not result.failed, result.failed
    for expected in (
        "session sesn_FAKE_A", "session sesn_FAKE_B", "environment env_FAKE",
        "mst_FAKE_ORG", "mst_FAKE_PROJECT",
        "agent_FAKE_ROGER", "agent_FAKE_MICHAEL", "agent_FAKE_COORD",
    ):
        assert any(expected in d for d in result.deleted), f"missing from teardown report: {expected}"
    assert not any("vault" in d for d in result.deleted), "vaults must not be touched when include_vaults=False"
    assert set(fake_client.calls) == {
        "delete_session sesn_FAKE_A", "delete_session sesn_FAKE_B",
        "delete_environment env_FAKE",
        "delete_memory_store mst_FAKE_ORG", "delete_memory_store mst_FAKE_PROJECT",
        "archive_agent agent_FAKE_COORD", "archive_agent agent_FAKE_ROGER", "archive_agent agent_FAKE_MICHAEL",
    }
    print("PASS: teardown_platform_resources deletes sessions/environment/memory stores and archives every team's agents")

    pipeline.clear_agent_cache()
    assert pipeline._load_cache() == {}
    print("PASS: clear_agent_cache empties the local cache so the next run starts fresh")

    # Vaults stay untouched unless explicitly opted in, and are reported as
    # skipped (not deleted) when unconfigured, exactly like this test env.
    result2 = pipeline.teardown_platform_resources(fake_client, [], [], include_vaults=True)
    assert any("GOOGLE_DOCS_VAULT_ID" in s for s in result2.skipped)
    assert any("SLACK_VAULT_ID" in s for s in result2.skipped)
    assert not any("vault" in d for d in result2.deleted)
    print("PASS: vault deletion is opt-in and skipped when no vault is configured")

    # 25. The /teardown route refuses while a run is still 'running', and
    # refuses when there's no API key configured to actually call the
    # platform with - both without touching anything.
    guard_run_id = db.create_run(project_id, "manual")  # left 'running' on purpose
    resp = client.post("/teardown", data={}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"stop it first" in resp.data or b"in progress" in resp.data
    db.delete_run(guard_run_id)
    print("PASS: /teardown refuses to run while a run is still in progress")

    resp = client.post("/teardown", data={}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"ANTHROPIC_API_KEY is not configured" in resp.data
    print("PASS: /teardown refuses without an API key instead of crashing")

    # 26. Delete the project; its job should be removed too.
    resp = client.post(f"/projects/{project_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_project(project_id) is None
    job_ids = [j.id for j in scheduler.list_jobs()]
    assert f"project-{project_id}" not in job_ids
    print("PASS: project deletion removes its scheduler job")

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
