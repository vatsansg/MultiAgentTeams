"""SQLite storage for projects, schedules, and run history.

Kept deliberately simple - a handful of functions over stdlib sqlite3, no
ORM - since the whole console is one user managing a handful of projects.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    brief TEXT NOT NULL,
    cron_expression TEXT,
    interval_minutes INTEGER,
    schedule_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trigger_type TEXT NOT NULL,          -- 'manual' | 'scheduled'
    status TEXT NOT NULL,                -- 'running' | 'success' | 'failed'
    satisfied INTEGER,                   -- 0/1/NULL
    session_id TEXT,
    brd_path TEXT,
    tdd_path TEXT,
    google_doc_links TEXT,
    cost_usd REAL,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# --- projects ---------------------------------------------------------


def create_project(name: str, brief: str, cron_expression: str = "", interval_minutes=None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, brief, cron_expression, interval_minutes, "
            "schedule_enabled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                name,
                brief,
                cron_expression or None,
                interval_minutes,
                1 if (cron_expression or interval_minutes) else 0,
                now_iso(),
            ),
        )
        return cur.lastrowid


def list_projects():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()


def get_project(project_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def update_schedule(project_id: int, cron_expression: str, interval_minutes, enabled: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET cron_expression = ?, interval_minutes = ?, "
            "schedule_enabled = ? WHERE id = ?",
            (cron_expression or None, interval_minutes, 1 if enabled else 0, project_id),
        )


def delete_project(project_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# --- runs ---------------------------------------------------------------


def create_run(project_id: int, trigger_type: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs (project_id, trigger_type, status, started_at) "
            "VALUES (?, ?, 'running', ?)",
            (project_id, trigger_type, now_iso()),
        )
        return cur.lastrowid


def finish_run(run_id: int, *, status: str, satisfied=None, session_id=None,
                brd_path=None, tdd_path=None, google_doc_links=None,
                cost_usd=None, error=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, satisfied = ?, session_id = ?, brd_path = ?, "
            "tdd_path = ?, google_doc_links = ?, cost_usd = ?, error = ?, finished_at = ? "
            "WHERE id = ?",
            (
                status,
                None if satisfied is None else int(satisfied),
                session_id,
                brd_path,
                tdd_path,
                google_doc_links,
                cost_usd,
                error,
                now_iso(),
                run_id,
            ),
        )


def get_run(run_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT runs.*, projects.name AS project_name FROM runs "
            "JOIN projects ON projects.id = runs.project_id WHERE runs.id = ?",
            (run_id,),
        ).fetchone()


def set_run_session_id(run_id: int, session_id: str):
    """Persisted as soon as the session exists (see run_manager's on_event
    handler), not just at finish_run() - so a still-running row can be
    looked up for a live cost estimate before it's done."""
    with get_conn() as conn:
        conn.execute("UPDATE runs SET session_id = ? WHERE id = ?", (session_id, run_id))


def update_run_cost(run_id: int, cost_usd: float):
    """Live cost refresh while a run is still going - only touches rows
    still 'running' so it can never clobber a finished run's final cost."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET cost_usd = ? WHERE id = ? AND status = 'running'",
            (cost_usd, run_id),
        )


def stop_run(run_id: int, message: str = "Stopped by user."):
    """Force a run to 'failed' - but only if it's still 'running', so this
    can never clobber a result the background thread finished writing right
    as the user clicked Stop."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET status = 'failed', error = ?, finished_at = ? "
            "WHERE id = ? AND status = 'running'",
            (message, now_iso(), run_id),
        )


def delete_run(run_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))


def list_all_session_ids() -> list:
    """Every distinct non-null session id this app has ever created, across
    all runs - not just the last N shown on the dashboard. Used by the
    platform teardown action (see pipeline.teardown_platform_resources) to
    find every session it needs to delete."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT session_id FROM runs WHERE session_id IS NOT NULL"
        ).fetchall()
        return [r["session_id"] for r in rows]


def has_running_run() -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM runs WHERE status = 'running' LIMIT 1").fetchone() is not None


def fail_orphaned_running_runs() -> int:
    """Called once at app startup. A run can be left 'running' forever if
    the Flask process is stopped or crashes while its background thread is
    mid-flight - nothing will ever call finish_run() for it again once the
    process is gone. Fail any such leftover rows so a restart doesn't leave
    the dashboard showing a permanently stuck "running" run. Returns how
    many rows were reconciled."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE runs SET status = 'failed', error = ?, finished_at = ? "
            "WHERE status = 'running'",
            (
                "Run did not finish - the server was stopped or restarted "
                "while this run was still in progress.",
                now_iso(),
            ),
        )
        return cur.rowcount


def list_runs(limit: int = 50):
    # started_at has one-second resolution, so two runs kicked off in the
    # same second tie under ORDER BY started_at DESC alone - id DESC breaks
    # the tie consistently (higher id = created later).
    with get_conn() as conn:
        return conn.execute(
            "SELECT runs.*, projects.name AS project_name FROM runs "
            "JOIN projects ON projects.id = runs.project_id "
            "ORDER BY runs.started_at DESC, runs.id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def list_runs_for_project(project_id: int, limit: int = 20):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE project_id = ? ORDER BY started_at DESC, id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
