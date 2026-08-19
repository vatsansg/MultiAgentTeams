# Agent Console

A small Flask app that puts the ClaudeMultiAgent_ManagedAgent BA-to-Architect
delivery pipeline behind a login, a visual dashboard, an on-demand "Run now" button, and a
schedule you can set per project - so the agents can run without you
opening a notebook.

> **For step-by-step setup and day-to-day operating instructions, see
> [RUNBOOK.md](RUNBOOK.md).** This file describes what the app is and how
> it's put together; the runbook tells you exactly what to do, in order.

## What this is

This app calls the *same* Managed Agents logic as
`../labs/ClaudeMultiAgent_ManagedAgent/ClaudeMultiAgent_ManagedAgent.ipynb` - a coordinator agent
("Delivery Lead") delegates to two specialists (Roger the Business
Analyst, Michael the Solution Architect), two memory stores ride along, an
outcome rubric grades the result and loops on failure, and the coordinator
files both finished documents to Google Docs and posts Slack updates via
vault-backed MCP servers. `pipeline.py` factors that logic out of the
notebook so it can run headless - triggered by a dashboard button click or
a scheduler tick, instead of a person stepping through notebook cells.

The problem this solves: the notebook is great, but a notebook can't
be clicked by someone who isn't a developer, and it can't run itself at
6am on a schedule. Agent Console turns that same pipeline into an
always-available internal tool with a UI.

## What you get

- **Login** - one username/password from environment variables, session
  cookie. Not multi-user; this is your own personal console.
- **Agent roster view** - an org chart, not a flat list: Delivery Lead
  (coordinator) on top, every specialist (Roger the BA, Michael the
  Architect, and any role added later) on the level below, each with role,
  description, tools, and whether it's been created yet on the Managed
  Agents platform. The roster is read live from `pipeline.py`'s
  `SPECIALISTS` list - adding a new specialist there adds a new card here
  automatically, no dashboard code changes required.
- **Projects** - each project is one website brief. Click "Run now" for an
  on-demand delivery, or give it a cron expression / an interval so it
  runs automatically.
- **Run history** - every run (manual or scheduled), its status, whether
  the outcome rubric was satisfied, an estimated cost, and where the BRD
  and Technical Design Document were saved. The table polls itself every
  4 seconds so a "running" row updates to "success"/"failed" without a
  page reload. A running row's cost also refreshes every 30 seconds with a
  live estimate straight from the platform (marked "(live)"), not just the
  final number once the run finishes.
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
4. **The agents** (coordinator + every specialist) - **archived**, not
   deleted. Managed Agents has no agent-delete endpoint, only archive,
   which is permanent: the agent becomes read-only forever and there's no
   way to unarchive it. This is a platform limitation, not a design choice
   here - archiving still stops the agent from being usable for any new
   session, which is what matters for cost.

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
behind a session-based login. `dashboard.py` holds the routes for viewing
the roster, creating/running/scheduling/deleting projects, and two small
JSON endpoints the page polls. `run_manager.py` starts each pipeline run
in a background thread (so an HTTP request never blocks on a minute-long
agent run) and records live progress events in memory plus the final
result in SQLite via `db.py`. `scheduler.py` wraps APScheduler so enabled
project schedules run automatically, even if nobody is looking at the
dashboard. `pipeline.py` is the actual Managed Agents logic - agent/
environment/session creation, the outcome-driven run, file downloads -
factored out of the notebook so both a button click and a cron tick can
call the same code.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app factory, blueprint registration, scheduler startup |
| `config.py` | All settings, read from environment variables |
| `auth.py` | Single-user login, `login_required` decorator |
| `dashboard.py` | Routes: roster view, project CRUD, run trigger, schedule form, JSON polling |
| `run_manager.py` | Background-thread run orchestration, in-memory live log, SQLite result persistence |
| `pipeline.py` | The actual Managed Agents logic - same as `ClaudeMultiAgent_ManagedAgent.ipynb`, callable headlessly |
| `scheduler.py` | APScheduler wiring - cron/interval jobs per project |
| `db.py` | SQLite schema and queries for `projects` and `runs` |
| `templates/` | `base.html`, `login.html`, `dashboard.html` |
| `static/style.css` | All styling - no CSS framework |
| `tests/test_smoke.py` | End-to-end test with a mocked pipeline - no API key needed |
| `RUNBOOK.md` | Numbered, step-by-step setup and operating instructions |

## Customizing the agent roster

The roster is one level deep by platform design: **Delivery Lead**
(coordinator) on top, every specialist underneath it, delegating never goes
further than that. Both agent creation and the dashboard's org chart read
the same two objects from `pipeline.py`, so there's exactly one place to
edit:

1. Write the new role's system prompt as a new constant in
   `../labs/shared/prompts.py` (follow `BA_SPECIALIST_SYSTEM` or
   `ARCHITECT_SPECIALIST_SYSTEM` as a template - non-interactive, makes
   labeled assumptions instead of asking questions, writes its output to a
   fixed path).
2. Import that constant at the top of `pipeline.py`, then append one entry
   to `SPECIALISTS`:
   ```python
   {
       "key": "dev",
       "cache_key": "dev_id",
       "agent_name": "Delivery Developer (Dana)",
       "display_name": "Dana",
       "role": "Developer",
       "description": "Turns the Technical Design Document into a scaffolded repo.",
       "system": DEVELOPER_SPECIALIST_SYSTEM,
       "tools_label": "write, read, bash (scoped)",
       "tools": [...],  # omit to reuse the default write/read-only toolset
   }
   ```
3. Update `DELIVERY_COORDINATOR_SYSTEM`'s numbered steps in `prompts.py` so
   the coordinator's own instructions say when to delegate to the new
   specialist and what to hand it - that's a workflow description in
   English, not something the roster list can infer on its own.

That's it. `_get_or_create_agents()` will create and cache the new agent,
add it to the coordinator's `multiagent.agents` roster, and the dashboard's
org chart will render a new card at the specialist level automatically -
no changes to `dashboard.py`, `dashboard.html`, or `style.css` needed
(colors cycle through a 5-color palette by roster position). Delete the
existing `agent_cache.json` file (or the matching key from it) first if
you want a role's agent recreated from scratch rather than reused.

## How scheduling actually works

This app uses **its own scheduler** (`APScheduler`'s `BackgroundScheduler`,
running inside the Flask process), not the Managed Agents platform's
native Scheduled Deployments feature. Schedules are fully visible and
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
