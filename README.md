# Agent Console

A small Flask app that puts the ClaudeMultiAgent_ManagedAgent BA-to-Architect
delivery pipeline behind a login, a visual dashboard, an on-demand "Run now" button, and a
schedule you can set per project - so the agents can run without you
opening a notebook.

> **For step-by-step setup and day-to-day operating instructions, see
> [RUNBOOK.md](RUNBOOK.md).** This file describes what the app is and how
> it's put together; the runbook tells you exactly what to do, in order.

## What this is

This app started from the *same* Managed Agents logic as
`../labs/ClaudeMultiAgent_ManagedAgent/ClaudeMultiAgent_ManagedAgent.ipynb` - a coordinator agent
delegates to specialists, two memory stores ride along, an outcome rubric
grades the result and loops on failure, and the coordinator files finished
documents to Google Docs and posts Slack updates via vault-backed MCP
servers. `pipeline.py` factors that logic out of the notebook so it can
run headless - triggered by a dashboard button click or a scheduler tick,
instead of a person stepping through notebook cells.

Since then, the fixed two-specialist roster has become a **DB-backed
catalog of agent roles + Teams**: an `agent_specs` table holds every
available role - Delivery Lead (coordinator), Business Analyst "Roger",
Solution Architect "Michael", a full-stack Developer "Smith" who turns a
Technical Design Document into a locally-runnable website, and two QA
Testers - "Jack" (tests in the local dev environment) and "Donald" (takes
Jack's artifacts and extends testing in the shared cloud sandbox
environment) - and a "Team" is a named snapshot of a *chosen subset* of
that catalog that a project picks before it can run (choose roles at
creation, or add/remove them later from the team's Edit page). See "Teams
and the agent catalog" below and `docs/multi-agent-teams.md` for the full
design.

The problem this solves: the notebook is great, but a notebook can't
be clicked by someone who isn't a developer, it can't run itself at 6am
on a schedule, and it can't offer more than one fixed pipeline shape.
Agent Console turns the same underlying platform primitives into an
always-available internal tool with a UI, multiple reusable teams, and a
non-technical scheduling picker.

## What you get

- **Login** - one username/password from environment variables, session
  cookie. Not multi-user; this is your own personal console.
- **Agents catalog** (sidebar: Agents) - a read-only view of every agent
  role available, its description, skills, and default tools.
- **Teams** (sidebar: Teams) - create a named, described team by checking
  which roles it should include (Delivery Lead is always included as
  coordinator) and, optionally, giving each role its own name **for this
  team** (names just need to be unique within the team - the same name is
  fine on two different teams); **Edit** an existing team to add/remove
  specialists or rename any of them later. A team can't be deleted while
  a project still uses it.
- **Agent Specs** (sidebar: Agent Specs) - edit a role's description,
  skills, and system prompt, with a rule-based "Validate" check before
  saving. Editing a spec only affects *new* teams - a team's
  already-provisioned agent keeps the prompt/skills it was created with.
- **Dashboard org chart** - pick a team at the top of the dashboard and
  its actual members render below as an org chart: coordinator on top,
  every specialist on the level below, each with role, description,
  tools, and whether it's been created yet on the Managed Agents platform.
- **Projects** - each project picks a team, then a website brief. Click
  "Run now" for an on-demand delivery, or set a plain-language schedule
  (every day / every week on a chosen day / every month on a chosen day /
  every N minutes, 12- or 24-hour clock) so it runs automatically - no
  cron syntax.
- **Run history** - every run (manual or scheduled), which team ran it,
  its status, whether the outcome rubric was satisfied, an estimated
  cost, and where the BRD, Technical Design Document, and/or website
  zip were saved. The table polls itself every 4 seconds so a "running"
  row updates to "success"/"failed" without a page reload. A running
  row's cost also refreshes every 30 seconds with a live estimate
  straight from the platform (marked "(live)"), not just the final
  number once the run finishes.
- **Stop / Delete on each run** - a running row gets a **Stop** button
  (marks it failed immediately; see "Stopping a run" below for what this
  does and doesn't guarantee), a finished row gets a **Delete** button to
  clear it from history.
- **Orphaned-run recovery** - if the Flask process is killed or restarted
  while a run is mid-flight, that row would otherwise be stuck showing
  "running" forever (nothing is left to ever finish it). Every app startup
  sweeps the database and marks any such leftover row "failed" automatically.
- **Danger zone: platform teardown** - a "Delete platform resources" button
  that opens a confirmation popup (not a browser `confirm()`) listing
  exactly what it's about to remove, then deletes every session this app
  created, the shared environment, and every memory store (org-standards
  plus one per project), and archives the agents - see "Tearing down
  platform resources" below for what "archive" means and why vaults are a
  separate, opt-in checkbox inside that popup.

### Stopping a run

Managed Agents sessions run on the platform, not inside this Flask process
- there's no confirmed API to force-terminate one from the client side.
Clicking **Stop** does two things: it immediately marks the run "failed" in
the dashboard (so you're never stuck watching a "running" row), and it sets
a cooperative flag that the background thread checks between streamed
events and exits on if it notices. If the platform is still mid-turn when
you click Stop, it will simply keep going or idle out server-side with
nothing further recorded here - Stop unblocks *your dashboard*, not
necessarily the remote session.

### Tearing down platform resources

The "Delete platform resources" button in the dashboard's Danger zone
section removes everything this app has created on Managed Agents, in the
order the platform requires:

1. **Every session** this app ever created (read from run history, not
   just the ones currently shown) - deleted. A currently-running run blocks
   this entirely; stop it first.
2. **The shared environment** - deleted. (Sessions have to go first: the
   platform won't delete an environment anything still references.)
3. **Every memory store** - the org-standards store plus every per-project
   store - deleted.
4. **The agents** - every team's coordinator and every specialist -
   **archived**, not deleted. Managed Agents has no agent-delete endpoint,
   only archive, which is permanent: the agent becomes read-only forever
   and there's no way to unarchive it. This is a platform limitation, not
   a design choice here - archiving still stops the agent from being
   usable for any new session, which is what matters for cost.

After teardown, the local agent cache is cleared automatically, so the
next run creates everything fresh rather than trying to reuse
now-archived/deleted ids.

**Vaults are not included by default.** The Google Docs and Slack vaults
aren't created by this app - you connect them yourself in Console - so
deleting them is a separate opt-in checkbox inside the confirmation popup.
Checking it revokes those vaults' stored OAuth credentials; getting them
back means reconnecting in Console, not just re-running this app. Vaults
also don't really drive ongoing cost by existing (they only hold
credentials), so there's rarely a reason to check that box.

Every step in a teardown is independent and best-effort: one resource
already being gone, or one call failing, doesn't stop the rest from being
attempted, and the dashboard reports exactly what succeeded, what was
skipped (nothing cached to remove), and what failed.

## Architecture, in one paragraph

`app.py` is the Flask app factory. `auth.py` gates every dashboard route
behind a session-based login. `dashboard.py` holds the routes for the
agents catalog, teams, agent-spec editing, project CRUD, run triggering,
schedule forms, and the JSON endpoints the page polls. `run_manager.py`
starts each pipeline run in a background thread (so an HTTP request never
blocks on a minute-long agent run), loads the project's team via
`db.get_team_with_members`, and records live progress events in memory
plus the final result in SQLite via `db.py`. `scheduler.py` wraps
APScheduler so enabled project schedules run automatically, translating
the dashboard's plain-language picker into real `CronTrigger`/
`IntervalTrigger` objects. `pipeline.py` is the actual Managed Agents
logic - team-driven agent/environment/session creation, the dynamically
assembled coordinator prompt and rubric, the outcome-driven run, file
downloads - built on the same primitives as the notebook but generalized
to any team composition instead of one fixed pair of specialists.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app factory, blueprint registration, scheduler startup |
| `config.py` | All settings, read from environment variables |
| `auth.py` | Single-user login, `login_required` decorator |
| `dashboard.py` | Routes: agents catalog, teams, agent-spec editor, project CRUD, run trigger, schedule form, JSON polling |
| `run_manager.py` | Background-thread run orchestration, in-memory live log, SQLite result persistence |
| `pipeline.py` | The team-driven Managed Agents logic - agent/environment/session creation, dynamic coordinator prompt + rubric assembly, callable headlessly |
| `scheduler.py` | APScheduler wiring - parses the dashboard's structured schedule fields into daily/weekly/monthly/interval triggers |
| `db.py` | SQLite schema and queries for `agent_specs`, `teams`, `team_members`, `projects`, and `runs` |
| `agent_specs_seed.py` | Default catalog (Delivery Lead, Roger, Michael, Smith, Jack, Donald) - each role seeded into `agent_specs` idempotently on every startup, so a new role added here backfills into an existing database automatically |
| `templates/` | `base.html` (topbar + sidebar shell), `login.html`, `dashboard.html`, `agents.html`, `teams.html`, `team_new.html`, `team_edit.html`, `agent_specs.html`, `agent_spec_edit.html` |
| `static/style.css` | All styling - no CSS framework |
| `tests/test_smoke.py` | End-to-end test with a mocked pipeline - no API key needed |
| `RUNBOOK.md` | Numbered, step-by-step setup and operating instructions |
| `docs/multi-agent-teams.md` | Full design notes for the agent catalog / teams / Developer role / scheduler feature |

## Teams and the agent catalog

The old fixed two-specialist roster (`pipeline.COORDINATOR`/
`pipeline.SPECIALISTS` Python constants) is gone, replaced by a DB-backed
model:

- **`agent_specs`** is the catalog - one row per available role
  (`delivery_lead`, `business_analyst`, `solution_architect`,
  `developer`, `qa_local`, `qa_cloud`), each with a `system_prompt`,
  `handoff_instructions` (used to build the coordinator's delegation step
  - see below), `skills`, and `tools_label`. Seeded idempotently, by
  `role_key`, on *every* startup from `agent_specs_seed.py` - a role
  already in the table is never touched, but a role in
  `DEFAULT_AGENT_SPECS` that isn't in the table yet gets inserted, so
  adding a new role there backfills automatically into an existing
  database (no wipe needed - this is how Jack and Donald were added
  after Smith already shipped). Edit a row's text/skills/prompt from the
  dashboard's **Agent Specs** page (with a rule-based "Validate" check);
  `role_key` and coordinator status aren't editable there.
- **`teams`** + **`team_members`** - creating a team (**Teams** page)
  lets you check which roles to include (Delivery Lead is always
  included as coordinator) and snapshots just those `agent_specs` rows
  into `team_members` at that moment. **Edit** an existing team
  (**Teams** page) to add or remove specialist roles afterward - if
  membership actually changes, the team's coordinator's cached platform
  agent id is invalidated (`pipeline.invalidate_cached_agent`) so it's
  recreated with the new roster and system prompt the next time that
  team runs (its `multiagent.agents` list and delegation steps are only
  assembled at agent-creation time - editing `team_members` alone doesn't
  retroactively change an already-provisioned coordinator). Editing the
  catalog itself only affects teams created *after* the edit - an
  existing team's already-provisioned specialist agents keep whatever
  prompt they were created with (same recreate-to-update caveat
  "Customizing..." below always had, just now scoped per team instead of
  globally). Each member gets a team-namespaced `cache_key`
  (`team<id>_<role_key>`) in `data/agent_cache.json`, so two teams both
  having e.g. a `business_analyst` never collide.
- **Per-team agent renaming** - each role can be given its own display
  name for a given team (create or edit form: a text field per role,
  defaulting to the catalog name), enforced unique *within that team*
  only (case-insensitive) - the same name is fine reused on a different
  team. A rename text-substitutes the role's original catalog name for
  the new one everywhere it appears in that role's own
  `system_prompt`/`handoff_instructions` **and** in every other included
  member's prompt text that mentions it by name (e.g. Donald's own prompt
  literally says "Jack's artifacts" - renaming Jack updates that
  reference too). Because a rename can touch more than one member's
  prompt, editing a name on an existing team invalidates **every**
  member's cached platform agent id, not just the coordinator's - see
  `docs/multi-agent-teams.md` for the full reasoning.
- **The coordinator's system prompt is assembled at agent-creation time**,
  not stored verbatim: `pipeline._build_coordinator_system()` takes the
  coordinator's stored template (which contains the literal tokens
  `{{DELEGATION_STEPS}}` and `{{CLOSING_STEPS}}`) and substitutes in a
  numbered delegation list built from each specialist's
  `handoff_instructions`, in `sequence` order, plus closing steps that
  are conditional on the team's actual composition (e.g. a Developer-only
  team's coordinator isn't told to file a BRD nobody produced, and a
  team with no QA role isn't told to deliver QA artifacts).
- **The Developer role ("Smith")** and both QA roles ("Jack"/"Donald")
  get the full, unscoped tool set (including bash) instead of the
  BA/Architect's scoped write/read-only set, because they need to
  install dependencies and actually run the site (Smith to build it,
  Jack/Donald to test it). The shared sandbox environment's
  `allowed_hosts` includes the npm/PyPI/Maven/NuGet registries and
  GitHub, and `allow_package_managers` is enabled - broadened once, in
  place, for every team (see `docs/multi-agent-teams.md` for the
  reasoning). Smith packages the finished site as
  `/mnt/session/outputs/site.zip`; Jack (local QA) and then Donald (cloud
  QA, building on Jack's artifacts) package their test plan, test cases,
  and QA report(s) as `/mnt/session/outputs/qa-artifacts.zip` - both
  served as `.zip` downloads next to the BRD/TDD links in run history.

## How scheduling actually works

This app uses **its own scheduler** (`APScheduler`'s `BackgroundScheduler`,
running inside the Flask process), not the Managed Agents platform's
native Scheduled Deployments feature. There's no cron syntax in the UI:
the dashboard's schedule picker collects a frequency (daily / weekly on a
chosen weekday / monthly on a chosen day-of-month / every N minutes), a
12- or 24-hour clock, and hour/minute/second, and `scheduler.parse_schedule_form()`
turns that into a structured schedule stored on the `projects` row
(`schedule_frequency`, `schedule_weekday`, `schedule_month_day`,
`schedule_hour`, `schedule_minute`, `schedule_second` - hour always stored
in 24-hour canonical form regardless of which clock mode was used to
enter it). `scheduler.add_or_update_job()` builds a real APScheduler
`CronTrigger`/`IntervalTrigger` directly from those fields (weekday
matched by name, e.g. `'wed'`, not by number, to avoid any
Sunday-vs-Monday-first ambiguity). Schedules are fully visible and
editable from this dashboard, but the Flask process must be running for
scheduled runs to fire - see RUNBOOK.md Step 11 for what that means in
practice and how to keep it running unattended.

## Security notes

This is a personal console, not a production multi-tenant app: single
shared login, plaintext-in-`.env` credentials (standard for local dev, not
for a public server), no CSRF protection on the forms, no rate limiting on
`/login`. If you ever expose this beyond your own machine, put it behind a
reverse proxy with TLS at minimum, and consider swapping `auth.py` for
Flask-Login with hashed passwords per the "multi-user" option you didn't
pick this time.
