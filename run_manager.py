"""Bridges Flask routes / the scheduler to pipeline.py.

Runs the (potentially slow - a minute or more) Managed Agents pipeline in a
background thread so an HTTP request or a scheduler tick never blocks on
it. Live progress events are kept in memory per run id so the dashboard can
poll them; the final outcome is persisted to SQLite via db.py.
"""
from __future__ import annotations

import threading
from typing import Callable

import db
import pipeline

# run_id -> list of event dicts, most recent last. Cleared lazily; fine for
# a single-user console where run volume is low.
_RUN_LOGS: dict[int, list] = {}

# run_id -> threading.Event, present only while that run's background
# thread is alive. Setting the event is a cooperative stop signal that
# pipeline.py checks between streamed events - see request_stop() below.
_CANCEL_EVENTS: dict[int, threading.Event] = {}

_LOCK = threading.Lock()

# Allows tests to swap in a fake pipeline without touching this module's
# internals - see tests/test_smoke.py.
_PIPELINE_FN: Callable = pipeline.run_delivery_pipeline


def set_pipeline_fn(fn: Callable):
    """Override the pipeline function - used by tests to mock out real API calls."""
    global _PIPELINE_FN
    _PIPELINE_FN = fn


def get_run_log(run_id: int) -> list:
    with _LOCK:
        return list(_RUN_LOGS.get(run_id, []))


def _append_log(run_id: int, event: dict):
    with _LOCK:
        _RUN_LOGS.setdefault(run_id, []).append(event)


def start_run(project_id: int, trigger_type: str) -> int:
    """Create a run record and kick off the pipeline in a background thread.

    Returns the new run id immediately; the caller (a Flask route) should
    redirect/respond right away and let the dashboard poll for status.
    """
    project = db.get_project(project_id)
    if project is None:
        raise ValueError(f"No project with id {project_id}")

    team = db.get_team_with_members(project["team_id"]) if project["team_id"] else None
    run_id = db.create_run(project_id, trigger_type, team_name=(team["name"] if team else None))

    if team is None:
        # No team assigned (a legacy project from before teams existed, or
        # one whose team was deleted out from under it). Still create the
        # run row - a visible history entry - and fail it immediately with
        # a readable error, same as any other configuration problem this
        # app surfaces, rather than raising before any row exists.
        db.finish_run(
            run_id, status="failed",
            error="This project has no team assigned. Assign a team, then run it again.",
        )
        return run_id

    cancel_event = threading.Event()
    with _LOCK:
        _CANCEL_EVENTS[run_id] = cancel_event

    def worker():
        def on_event(event):
            _append_log(run_id, event)
            # Persist the session id as soon as it exists, not just at the
            # end - lets the dashboard fetch a live cost estimate and lets
            # Stop/orphan-reconciliation reason about this run before it's
            # finished.
            if event.get("kind") == "session_created" and event.get("session_id"):
                db.set_run_session_id(run_id, event["session_id"])

        try:
            result = _PIPELINE_FN(
                project["name"], project["brief"], team,
                on_event=on_event, should_stop=cancel_event.is_set,
            )
            db.finish_run(
                run_id,
                status="success",
                satisfied=result.satisfied,
                session_id=result.session_id,
                brd_path=result.brd_path,
                tdd_path=result.tdd_path,
                site_path=result.site_path,
                qa_artifacts_path=result.qa_artifacts_path,
                cost_usd=result.cost_usd,
            )
        except pipeline.PipelineStopped as exc:
            _append_log(run_id, {"kind": "status", "message": str(exc)})
            db.stop_run(run_id, str(exc) or "Stopped by user.")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the dashboard
            _append_log(run_id, {"kind": "error", "message": str(exc)})
            db.finish_run(run_id, status="failed", error=str(exc))
        finally:
            with _LOCK:
                _CANCEL_EVENTS.pop(run_id, None)

    thread = threading.Thread(target=worker, name=f"run-{run_id}", daemon=True)
    thread.start()
    return run_id


def request_stop(run_id: int):
    """Best-effort stop for a running run.

    Sets the cooperative cancel flag the background thread's should_stop
    callback polls (see pipeline.py's event loop) - but also marks the run
    'failed' in the database immediately (via db.stop_run, which is a no-op
    unless the row is still 'running'), so the dashboard reflects the stop
    right away instead of waiting on the thread to notice. The thread, if
    it's genuinely still alive, will exit on its own shortly after; if it
    already finished or the process that owned it is gone, this is still
    safe to call - there's simply no live cancel_event to signal.
    """
    with _LOCK:
        cancel_event = _CANCEL_EVENTS.get(run_id)
    if cancel_event:
        cancel_event.set()
    _append_log(run_id, {"kind": "status", "message": "Stop requested by user"})
    db.stop_run(run_id, "Stopped by user.")


def get_live_cost(run_id: int):
    """Live cost estimate for a still-running run, for the dashboard's 30s
    poll. Returns None (leaving the last displayed value alone) if the run
    isn't running, has no session id yet, or the platform call fails.
    """
    run = db.get_run(run_id)
    if run is None or run["status"] != "running" or not run["session_id"]:
        return None
    cost_usd = pipeline.get_live_cost(run["session_id"])
    if cost_usd is not None:
        db.update_run_cost(run_id, cost_usd)
    return cost_usd
