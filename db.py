"""SQLite storage for agent specs, teams, projects, schedules, and run
history.

Kept deliberately simple - a handful of functions over stdlib sqlite3, no
ORM - since the whole console is one user managing a handful of projects.

There is no migrations framework: schema changes to existing tables go
through `_add_column_if_missing()` (a defensive `ALTER TABLE`, safe to run
against both a fresh DB and one created before the column existed), called
from `init_db()`. New tables just go in `SCHEMA` as `CREATE TABLE IF NOT
EXISTS`.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role_title TEXT NOT NULL,
    description TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    handoff_instructions TEXT,
    skills TEXT NOT NULL DEFAULT '[]',
    tools_label TEXT NOT NULL DEFAULT '',
    is_coordinator INTEGER NOT NULL DEFAULT 0,
    sequence INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    agent_spec_id INTEGER REFERENCES agent_specs(id) ON DELETE SET NULL,
    role_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role_title TEXT NOT NULL,
    description TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    handoff_instructions TEXT,
    skills TEXT NOT NULL DEFAULT '[]',
    tools_label TEXT NOT NULL DEFAULT '',
    is_coordinator INTEGER NOT NULL DEFAULT 0,
    sequence INTEGER NOT NULL DEFAULT 0,
    cache_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(team_id, role_key)
);

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


def _add_column_if_missing(conn, table: str, column: str, coltype: str):
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _add_column_if_missing(conn, "projects", "team_id", "INTEGER REFERENCES teams(id)")
        _add_column_if_missing(conn, "projects", "schedule_frequency", "TEXT")
        _add_column_if_missing(conn, "projects", "schedule_weekday", "INTEGER")
        _add_column_if_missing(conn, "projects", "schedule_month_day", "INTEGER")
        _add_column_if_missing(conn, "projects", "schedule_hour", "INTEGER")
        _add_column_if_missing(conn, "projects", "schedule_minute", "INTEGER")
        _add_column_if_missing(conn, "projects", "schedule_second", "INTEGER")
        _add_column_if_missing(conn, "projects", "schedule_summary", "TEXT")
        _add_column_if_missing(conn, "runs", "team_name", "TEXT")
        _add_column_if_missing(conn, "runs", "site_path", "TEXT")
    _seed_agent_specs_if_empty()


# --- agent_specs ---------------------------------------------------------


class DuplicateNameError(Exception):
    """Raised by create_team when the name UNIQUE constraint is violated,
    so callers can catch a specific, expected error instead of a raw
    sqlite3.IntegrityError."""


def list_agent_specs():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM agent_specs ORDER BY is_coordinator DESC, sequence ASC, id ASC"
        ).fetchall()


def get_agent_spec(spec_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM agent_specs WHERE id = ?", (spec_id,)).fetchone()


def get_agent_spec_by_role_key(role_key: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM agent_specs WHERE role_key = ?", (role_key,)
        ).fetchone()


def update_agent_spec(spec_id: int, *, display_name, role_title, description,
                       system_prompt, handoff_instructions, skills, tools_label):
    # role_key and is_coordinator are intentionally not parameters here -
    # the Agent Specs UI never exposes them, so "exactly one coordinator"
    # stays true by construction rather than a runtime check.
    with get_conn() as conn:
        conn.execute(
            "UPDATE agent_specs SET display_name = ?, role_title = ?, description = ?, "
            "system_prompt = ?, handoff_instructions = ?, skills = ?, tools_label = ?, "
            "updated_at = ? WHERE id = ?",
            (display_name, role_title, description, system_prompt, handoff_instructions,
             skills, tools_label, now_iso(), spec_id),
        )


def _seed_agent_specs_if_empty():
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM agent_specs").fetchone()["c"]
        if count:
            return
        from agent_specs_seed import DEFAULT_AGENT_SPECS
        now = now_iso()
        for spec in DEFAULT_AGENT_SPECS:
            conn.execute(
                "INSERT INTO agent_specs (role_key, display_name, role_title, description, "
                "system_prompt, handoff_instructions, skills, tools_label, is_coordinator, "
                "sequence, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (spec["role_key"], spec["display_name"], spec["role_title"], spec["description"],
                 spec["system_prompt"], spec["handoff_instructions"], spec["skills"],
                 spec["tools_label"], spec["is_coordinator"], spec["sequence"], now, now),
            )


# --- teams -----------------------------------------------------------------


def _insert_team_member(conn, team_id: int, spec) -> None:
    cache_key = f"team{team_id}_{spec['role_key']}"
    conn.execute(
        "INSERT INTO team_members (team_id, agent_spec_id, role_key, display_name, "
        "role_title, description, system_prompt, handoff_instructions, skills, "
        "tools_label, is_coordinator, sequence, cache_key, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (team_id, spec["id"], spec["role_key"], spec["display_name"], spec["role_title"],
         spec["description"], spec["system_prompt"], spec["handoff_instructions"],
         spec["skills"], spec["tools_label"], spec["is_coordinator"], spec["sequence"],
         cache_key, now_iso()),
    )


def create_team(name: str, description: str, spec_ids: list = None) -> int:
    """Creates the team, then snapshots the chosen agent_specs rows into
    team_members in the same transaction. The coordinator role is always
    included regardless of spec_ids (there's no UI to opt it out).
    spec_ids=None (the default) includes every current catalog role -
    kept for backward compatibility with callers that want "everything"."""
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO teams (name, description, created_at) VALUES (?, ?, ?)",
                (name, description, now_iso()),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateNameError(str(exc)) from exc
        team_id = cur.lastrowid
        specs = conn.execute("SELECT * FROM agent_specs ORDER BY sequence ASC, id ASC").fetchall()
        chosen_ids = set(spec_ids) if spec_ids is not None else {s["id"] for s in specs}
        for spec in specs:
            if spec["id"] in chosen_ids or spec["is_coordinator"]:
                _insert_team_member(conn, team_id, spec)
        return team_id


def rename_team(team_id: int, name: str, description: str):
    with get_conn() as conn:
        try:
            conn.execute(
                "UPDATE teams SET name = ?, description = ? WHERE id = ?",
                (name, description, team_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateNameError(str(exc)) from exc


def update_team_members(team_id: int, spec_ids: list) -> bool:
    """Reconciles an existing team's membership with the given agent_spec
    ids - adds specs newly checked, removes specialists newly unchecked.
    The coordinator role is never removable and isn't affected by
    spec_ids either way. Returns True if anything actually changed, so
    the caller knows whether the team's coordinator needs to be recreated
    on the platform (its multiagent roster and dynamic system prompt are
    both built from the specialist list at agent-creation time - adding a
    specialist here doesn't retroactively update an already-provisioned
    coordinator, only invalidating its cached agent id and letting the
    next run recreate it does)."""
    spec_ids = set(spec_ids)
    changed = False
    with get_conn() as conn:
        current = conn.execute(
            "SELECT * FROM team_members WHERE team_id = ?", (team_id,)
        ).fetchall()
        current_spec_ids = {m["agent_spec_id"] for m in current if not m["is_coordinator"]}

        for member in current:
            if not member["is_coordinator"] and member["agent_spec_id"] not in spec_ids:
                conn.execute("DELETE FROM team_members WHERE id = ?", (member["id"],))
                changed = True

        to_add = spec_ids - current_spec_ids
        if to_add:
            specs_by_id = {
                s["id"]: s for s in conn.execute(
                    "SELECT * FROM agent_specs WHERE id IN ({})".format(
                        ",".join("?" * len(to_add))
                    ), tuple(to_add),
                ).fetchall()
            }
            for spec_id in to_add:
                spec = specs_by_id.get(spec_id)
                if spec is None or spec["is_coordinator"]:
                    continue  # coordinator membership isn't toggleable here
                _insert_team_member(conn, team_id, spec)
                changed = True

    return changed


def get_team_coordinator_cache_key(team_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT cache_key FROM team_members WHERE team_id = ? AND is_coordinator = 1",
            (team_id,),
        ).fetchone()
        return row["cache_key"] if row else None


def list_teams():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM teams ORDER BY created_at DESC").fetchall()


def get_team(team_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()


def list_team_members(team_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM team_members WHERE team_id = ? "
            "ORDER BY is_coordinator DESC, sequence ASC",
            (team_id,),
        ).fetchall()


def get_team_with_members(team_id: int):
    """The bundle pipeline.py and run_manager.py actually consume:
    {"id":, "name":, "description":, "members": [...]}. Each member's
    `skills` JSON string is parsed into a list. Returns None if the team
    doesn't exist (e.g. a project's team_id is stale/None)."""
    team = get_team(team_id)
    if team is None:
        return None
    members = [dict(m) for m in list_team_members(team_id)]
    for m in members:
        try:
            m["skills"] = json.loads(m["skills"] or "[]")
        except json.JSONDecodeError:
            m["skills"] = []
    return {"id": team["id"], "name": team["name"], "description": team["description"], "members": members}


def team_in_use(team_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM projects WHERE team_id = ? LIMIT 1", (team_id,)
        ).fetchone() is not None


def delete_team(team_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))  # team_members cascades


def list_all_team_members():
    """Every team_members row across every team, joined to the team name -
    replaces the old fixed [COORDINATOR, *SPECIALISTS] iteration used by
    teardown and the dashboard's danger-zone summary."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT team_members.*, teams.name AS team_name FROM team_members "
            "JOIN teams ON teams.id = team_members.team_id "
            "ORDER BY team_members.team_id, team_members.is_coordinator DESC, team_members.sequence ASC"
        ).fetchall()


# --- projects ---------------------------------------------------------


def create_project(name: str, brief: str, team_id: int, schedule: dict = None) -> int:
    schedule = schedule or {}
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, brief, team_id, cron_expression, interval_minutes, "
            "schedule_frequency, schedule_weekday, schedule_month_day, schedule_hour, "
            "schedule_minute, schedule_second, schedule_summary, schedule_enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                brief,
                team_id,
                None,  # cron_expression - kept for backward compat, no longer written
                schedule.get("interval_minutes"),
                schedule.get("frequency"),
                schedule.get("weekday"),
                schedule.get("month_day"),
                schedule.get("hour"),
                schedule.get("minute"),
                schedule.get("second"),
                schedule.get("summary"),
                1 if schedule.get("frequency") else 0,
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


def update_schedule(project_id: int, schedule: dict):
    schedule = schedule or {}
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET cron_expression = ?, interval_minutes = ?, "
            "schedule_frequency = ?, schedule_weekday = ?, schedule_month_day = ?, "
            "schedule_hour = ?, schedule_minute = ?, schedule_second = ?, "
            "schedule_summary = ?, schedule_enabled = ? WHERE id = ?",
            (
                None,
                schedule.get("interval_minutes"),
                schedule.get("frequency"),
                schedule.get("weekday"),
                schedule.get("month_day"),
                schedule.get("hour"),
                schedule.get("minute"),
                schedule.get("second"),
                schedule.get("summary"),
                1 if schedule.get("frequency") else 0,
                project_id,
            ),
        )


def delete_project(project_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# --- runs ---------------------------------------------------------------


def create_run(project_id: int, trigger_type: str, team_name: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs (project_id, trigger_type, status, team_name, started_at) "
            "VALUES (?, ?, 'running', ?, ?)",
            (project_id, trigger_type, team_name, now_iso()),
        )
        return cur.lastrowid


def finish_run(run_id: int, *, status: str, satisfied=None, session_id=None,
                brd_path=None, tdd_path=None, site_path=None, google_doc_links=None,
                cost_usd=None, error=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, satisfied = ?, session_id = ?, brd_path = ?, "
            "tdd_path = ?, site_path = ?, google_doc_links = ?, cost_usd = ?, error = ?, "
            "finished_at = ? WHERE id = ?",
            (
                status,
                None if satisfied is None else int(satisfied),
                session_id,
                brd_path,
                tdd_path,
                site_path,
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
