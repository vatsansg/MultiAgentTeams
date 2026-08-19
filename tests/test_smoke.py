"""Smoke test for the Agent Console Flask app - verifies login, the
dashboard, project/schedule CRUD, and the on-demand run flow, all with the
real Managed Agents pipeline replaced by a fast fake. No Anthropic API key
or vault is needed to run this.

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


def fake_pipeline(project_name, project_brief, *, max_iterations=None, on_event=None, should_stop=None):
    """Stands in for pipeline.run_delivery_pipeline - no network calls."""
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
    assert b"Agent roster" in resp.data
    print("PASS: login succeeds and dashboard renders (agent roster visible)")

    # 4. Dashboard shows the fixed 3-agent roster.
    for name in (b"Delivery Lead", b"Roger", b"Michael"):
        assert name in resp.data, f"{name} missing from dashboard"
    print("PASS: all three agents appear on the dashboard")

    # 5. Create a project.
    resp = client.post(
        "/projects",
        data={
            "name": "Boutique Coffee Roastery Website",
            "brief": "Sell beans online, let people book roastery tours.",
            "cron_expression": "",
            "interval_minutes": "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    projects = db.list_projects()
    assert len(projects) == 1, f"expected 1 project, got {len(projects)}"
    project_id = projects[0]["id"]
    print(f"PASS: project created (id={project_id})")

    # 6. Trigger an on-demand run and wait for the background thread to finish.
    # Pre-create the files fake_pipeline "downloads", so the BRD/TDD links'
    # actual file-serving route (not just a href="#" placeholder) has real
    # content to check in step 7b below.
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
    print("PASS: on-demand run completes via the (fake) pipeline and is recorded in SQLite")

    # 7. Run log captured the fake pipeline's events.
    log = run_manager.get_run_log(run_row["id"])
    kinds = [e["kind"] for e in log]
    assert "thread_created" in kinds and "outcome_evaluation" in kinds
    print("PASS: live run log captured pipeline events")

    # 7b. The dashboard's BRD/TDD links actually serve the file - this used
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

    # 8. Update the schedule, confirm it's stored and a scheduler job exists.
    resp = client.post(
        f"/projects/{project_id}/schedule",
        data={"cron_expression": "", "interval_minutes": "60"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    updated = db.get_project(project_id)
    assert updated["schedule_enabled"] == 1
    assert updated["interval_minutes"] == 60
    print("PASS: schedule saved (every 60 minutes)")

    import scheduler
    job_ids = [j.id for j in scheduler.list_jobs()]
    assert f"project-{project_id}" in job_ids
    print("PASS: APScheduler job registered for the project")

    # 9. JSON polling endpoints work.
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json[0]["status"] == "success"
    resp = client.get(f"/api/runs/{run_row['id']}/log")
    assert resp.status_code == 200
    assert len(resp.json) == len(log)
    print("PASS: /api/runs and /api/runs/<id>/log respond correctly")

    # 10. A failing run stores its error, and the dashboard actually shows it.
    def failing_pipeline(project_name, project_brief, *, max_iterations=None, on_event=None, should_stop=None):
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

    # 11. Delete a finished (failed) run from history.
    resp = client.post(f"/runs/{failed_row['id']}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_run(failed_row["id"]) is None
    print("PASS: a finished run can be deleted from run history")

    # 12. A run that hangs (simulating a slow/stuck platform call) can be
    # stopped from the dashboard - and its session id shows up in the
    # database while it's still in progress, not just once it's done.
    def stallable_pipeline(project_name, project_brief, *, max_iterations=None,
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

    # 13. Live cost endpoint responds safely with no ANTHROPIC_API_KEY
    # configured in this test env - null, not an error.
    resp = client.get(f"/api/runs/{stall_run_id}/cost")
    assert resp.status_code == 200
    assert resp.json["cost_usd"] is None
    print("PASS: live cost endpoint responds safely with no API key configured")

    # 14. Delete refuses to remove a still-running run.
    resp = client.post(f"/runs/{stall_run_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_run(stall_run_id) is not None, "delete should refuse a still-running run"
    print("PASS: delete route refuses to remove a still-running run")

    # 15. Stop it - the dashboard should reflect this immediately.
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

    # 16. Now that it's finished (stopped), delete succeeds.
    resp = client.post(f"/runs/{stall_run_id}/delete", follow_redirects=True)
    assert db.get_run(stall_run_id) is None
    print("PASS: delete route removes a finished (stopped) run")

    # 17. Orphan reconciliation - simulates what happens on app startup if a
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

    # 18. pipeline.teardown_platform_resources deletes/archives every
    # resource it's told about, using a fake client so no real network call
    # happens. Seed a fake cache first so there's something to tear down.
    pipeline._save_cache({
        "roger_id": "agent_FAKE_ROGER",
        "michael_id": "agent_FAKE_MICHAEL",
        "coordinator_id": "agent_FAKE_COORD",
        "org_standards_id": "mst_FAKE_ORG",
        "environment_id": "env_FAKE",
    })
    fake_project_store = types.SimpleNamespace(id="mst_FAKE_PROJECT", name="delivery-project-boutique")
    fake_client = FakeAnthropicClient(project_stores=[fake_project_store])

    result = pipeline.teardown_platform_resources(
        fake_client, ["sesn_FAKE_A", "sesn_FAKE_B"], include_vaults=False,
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
    print("PASS: teardown_platform_resources deletes sessions/environment/memory stores and archives agents")

    pipeline.clear_agent_cache()
    assert pipeline._load_cache() == {}
    print("PASS: clear_agent_cache empties the local cache so the next run starts fresh")

    # Vaults stay untouched unless explicitly opted in, and are reported as
    # skipped (not deleted) when unconfigured, exactly like this test env.
    result2 = pipeline.teardown_platform_resources(fake_client, [], include_vaults=True)
    assert any("GOOGLE_DOCS_VAULT_ID" in s for s in result2.skipped)
    assert any("SLACK_VAULT_ID" in s for s in result2.skipped)
    assert not any("vault" in d for d in result2.deleted)
    print("PASS: vault deletion is opt-in and skipped when no vault is configured")

    # 19. The /teardown route refuses while a run is still 'running', and
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

    # 20. Delete the project; its job should be removed too.
    resp = client.post(f"/projects/{project_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_project(project_id) is None
    job_ids = [j.id for j in scheduler.list_jobs()]
    assert f"project-{project_id}" not in job_ids
    print("PASS: project deletion removes its scheduler job")

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
