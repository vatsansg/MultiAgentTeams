# Agent Console - Runbook

Numbered, do-this-then-that instructions for getting Agent Console running
and operating it day to day. For what the app *is* and how it's built, see
[README.md](README.md). Run every command from inside the `agent_console/`
folder unless a step says otherwise.

---

## Step 0 - Set up Visual Studio Code

Do this once, before Step 1. It gets VS Code pointed at the right folder,
the right Python interpreter, and gives you a one-click way to run both
the app and the smoke test.

**0.1 - Install VS Code and the Python extension.** Install VS Code from
[code.visualstudio.com](https://code.visualstudio.com) if you don't have
it. Open the Extensions view (`Ctrl+Shift+X`) and install **Python**
(`ms-python.python`) - this pulls in Pylance for autocomplete and the
interpreter picker. Also worth installing: **Jinja** (syntax highlighting
for `templates/*.html`) and **DotENV** (syntax highlighting for `.env`).

**0.2 - Open the right folder.** Use `File > Open Folder...` and select
the `agent_console` folder directly (not the whole `aioffering` folder) -
this is your workspace root, and it's what `${workspaceFolder}` refers to
in the config files this step creates.

```
File > Open Folder... > C:\vatsan\optimum\aioffering\agent_console
```

**0.3 - Open the integrated terminal.** `` Ctrl+` `` (backtick) opens a
terminal already `cd`'d into `agent_console`. Every command in this
runbook from Step 3 onward runs here. On Windows, VS Code's default
integrated terminal is PowerShell - the commands below show the
PowerShell form where it differs from macOS/Linux.

**0.4 - Create the virtual environment from inside VS Code.** You can do
this now (it's also covered again in Step 3, in case you're following the
runbook without VS Code):

```powershell
python -m venv venv
```

**0.5 - Select the interpreter.** Press `Ctrl+Shift+P`, type **Python:
Select Interpreter**, and choose the one inside `.\venv\` (it'll show as
something like `venv\Scripts\python.exe` or `('venv': venv)`). The Python
version shown in VS Code's bottom-left status bar should now say
`venv`, not your system Python. This matters - without it, VS Code's
"Run" button and the integrated terminal's `python` may point at a
different Python than the one with your installed dependencies.

**0.6 - Workspace config (already included).** This folder ships with a
`.vscode/` directory containing:
- `settings.json` - tells VS Code where the venv interpreter lives and to
  load `.env` automatically for anything you run/debug.
- `launch.json` - two ready-made **Run and Debug** configurations:
  **"Agent Console (app.py)"** (starts the Flask app under the debugger,
  breakpoints work) and **"Agent Console smoke test"** (runs
  `tests/test_smoke.py`).

To use them: open the **Run and Debug** view (`Ctrl+Shift+D`), pick a
configuration from the dropdown at the top, and press `F5` (or click the
green ▷). This is equivalent to typing `python app.py` or
`python tests/test_smoke.py` in the terminal, just with breakpoints and
variable inspection available.

**Outcome:** VS Code is open on the `agent_console` folder, the status bar
shows the `venv` interpreter, and pressing `F5` with **"Agent Console
smoke test"** selected successfully runs `tests/test_smoke.py` in the
Debug Console (this will only fully succeed once Steps 3-4 are also done -
that's expected at this point, this step just confirms VS Code itself is
wired up correctly).

---

## Step 1 - Confirm and gather prerequisites

Before you start, get every one of these in hand. Sub-steps below tell you
exactly where to find each one.

**1.1 - Python 3.11 or newer.**
```powershell
python --version
```
If it's missing or older, install from
[python.org/downloads](https://www.python.org/downloads/) (check "Add
python.exe to PATH" during install on Windows) or via the Microsoft Store.

**1.2 - An Anthropic API key with Managed Agents access.**
1. Sign in at [console.anthropic.com](https://console.anthropic.com).
2. Go to **API Keys** and click **Create Key**. Copy it immediately - it's
   only shown once. It looks like `sk-ant-...`.
3. Confirm your account/organization has **Managed Agents** and
   **multi-agent** access enabled. If agent or session creation later
   fails with an access-related error, this is usually why - request
   access through your Anthropic account team or Console if so.

**1.3 - A Google Docs Managed Agents vault.**
1. In Claude Console, go to **Managed Agents > Vaults**.
2. Click **Create vault** (or open an existing one you want to reuse).
3. Add a credential: choose the **Google Docs** / MCP OAuth option and
   follow the OAuth prompt to connect the Google account you want the
   agent to file documents as.
4. Copy the vault's id - it starts with `vlt_`. This is your
   `GOOGLE_DOCS_VAULT_ID`.

**1.4 - A Slack Managed Agents vault.**
1. Same **Managed Agents > Vaults** area - create a new vault or reuse the
   one from 1.3.
2. Add a credential: choose the **Slack** / MCP OAuth option and authorize
   the connection for your Slack workspace.
3. Copy this vault's id (also `vlt_...`) - this is your `SLACK_VAULT_ID`.
   If it's the *same* vault as 1.3, that's fine, one vault can hold
   multiple credentials.

**1.5 - A Slack channel the agent can post to.**
1. In Slack, pick or create a channel, e.g. `#delivery`.
2. Invite the Slack app/bot user connected in 1.4 to that channel
   (`/invite @your-app-name` in the channel) - otherwise the agent's
   `post_message` calls will fail even with a valid credential.
3. Note the exact channel name including the `#` - this is your
   `SLACK_CHANNEL`.

**Outcome:** you have a Python install, an Anthropic API key, two vault
ids, and a Slack channel name written down somewhere - everything Step 4
asks you to paste into `.env`.

---

## Step 2 - Confirm the folder layout

`pipeline.py` and `agent_specs_seed.py` import prompts/cost_meter/
cache_usage from `agent_console/agentprompts/` - an in-repo folder, not
an external dependency, so there's normally nothing to confirm here.

```bash
ls agent_console/agentprompts/prompts.py   # should exist
```

**Outcome:** confirms `config.AGENT_PROMPTS_DIR`'s default
(`agent_console/agentprompts`) will resolve. Only relevant if you've
relocated that folder - set the `AGENT_PROMPTS_DIR` environment variable
to point at wherever it actually lives.

This folder started as a copy of `../labs/shared/` (a separate,
un-versioned notebook project that used to be the only place these
prompts lived - see `docs/multi-agent-teams.md`). The two are now
independent: a prompt change meant for both has to be made in both
places by hand.

---

## Step 3 - Create a virtual environment and install dependencies

```bash
cd agent_console
python3 -m venv venv
```

Activating it depends on your shell - **Windows has no `venv/bin/`**, only
`venv/Scripts/`, so `source venv/bin/activate` will always fail there with
`No such file or directory`. Use the line matching your terminal:

| Shell | Command |
|---|---|
| macOS / Linux (bash/zsh) | `source venv/bin/activate` |
| Windows, Git Bash / MINGW64 (e.g. `AzureAD+user@host MINGW64`) | `source venv/Scripts/activate` |
| Windows, PowerShell | `.\venv\Scripts\Activate.ps1` |
| Windows, cmd.exe | `venv\Scripts\activate.bat` |

Your prompt (`MINGW64`) means you're in Git Bash, so:

```bash
source venv/Scripts/activate
```

Either way, your prompt should now show `(venv)` at the start. Then:

```bash
pip install -r requirements.txt
```

**Outcome:** `flask`, `apscheduler`, `anthropic`, and `python-dotenv` are
installed in an isolated environment. Run `pip list` to confirm all four
appear.

**If PowerShell refuses `Activate.ps1` with a script-execution error:** run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` once in that
PowerShell window, then retry - or just use Git Bash's `source
venv/Scripts/activate` instead, which doesn't hit this restriction.

---

## Step 4 - Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in every value:

| Variable | Required | What it's for |
|---|---|---|
| `CONSOLE_USERNAME` | yes | Your login username |
| `CONSOLE_PASSWORD` | yes | Your login password |
| `FLASK_SECRET_KEY` | yes | Signs the session cookie - use a random string |
| `ANTHROPIC_API_KEY` | yes | Authenticates to Claude / Managed Agents |
| `GOOGLE_DOCS_VAULT_ID` | yes | From Step 1.3, starts with `vlt_` |
| `SLACK_VAULT_ID` | yes | From Step 1.4, starts with `vlt_` |
| `SLACK_CHANNEL` | yes | From Step 1.5, e.g. `#delivery` |
| `MODEL` | no | Defaults to `claude-sonnet-5`; switch to `claude-haiku-4-5-20251001` for cheaper runs |
| `MAX_ITERATIONS` | no | Defaults to `2` - how many grading/fix loops the outcome rubric allows |
| `GOOGLE_DOCS_MCP_URL` / `SLACK_MCP_URL` | no | Only needed if a vault has more than one MCP credential and the app can't pick automatically |

`config.py` loads `.env` automatically via `python-dotenv` as soon as the
app starts - no manual `export` or `source .env` needed. A real
environment variable (e.g. set by a process manager) always overrides the
matching `.env` value.

**Outcome:** every value `config.py` reads has something real behind it.
Leaving `ANTHROPIC_API_KEY` blank is fine for Steps 5-7 (the smoke test
and login don't need it) but required from Step 9 onward.

---

## Step 5 - Verify the app works before spending any API credits

```bash
python tests/test_smoke.py
```

This replaces the real Managed Agents pipeline with a fast fake and
exercises the whole app - login, dashboard rendering, project creation, an
on-demand run completing and appearing in run history, schedule creation,
and project deletion - end to end through Flask's test client.

**Outcome:** you should see 11 lines starting with `PASS:` and a final
`SMOKE TEST PASSED`. If anything fails here, fix it before continuing -
every later step assumes this passes.

---

## Step 6 - Start the app

```bash
python app.py
```

**Outcome:** console output shows the Flask development server listening
on `http://0.0.0.0:5000`. Leave this terminal running - closing it stops
the app and, per Step 11, pauses scheduled runs.

---

## Step 7 - Log in

Open `http://localhost:5000` in a browser. Enter the `CONSOLE_USERNAME` /
`CONSOLE_PASSWORD` from your `.env`.

**Outcome:** you land on the dashboard. On first startup the app seeds a
default agent catalog (Delivery Lead, Roger the Business Analyst, Michael
the Solution Architect, Smith the Developer, Jack the local QA Tester,
Donald the cloud QA Tester) - but there are no teams yet, so you'll see a
prompt to create one before an org chart or project can exist. See Step
7b below, and README.md's "Teams and the agent catalog" section for the
full model.

---

## Step 7b - Create your first team

Sidebar → **Teams** → **+ Create team**. Give it a name (e.g. `Website
Delivery Team`) and an optional description. Below that, check which
roles to include - Delivery Lead is always included as coordinator; leave
every specialist checked (Roger, Michael, Smith, Jack, Donald) for a
full-pipeline team, or uncheck any you don't want yet - you can add/remove
roles later from the team's **Edit** page. Click **Create team**.

**Outcome:** back on the dashboard, the new team appears in the Team
selector at the top and its org chart renders below - Delivery Lead
(coordinator) on top, whichever specialists you checked on the level
below. Each agent card shows "not created yet" - that's expected until
Step 9's first real run.

---

## Step 8 - Create your first project

On the dashboard, open **"+ New project"**:

1. **Team** - defaults to whichever team is currently selected above; pick
   a different one if you have more than one.
2. **Project name** - short and specific, e.g. `Boutique Coffee Roastery Website`.
3. **Project brief** - as complete as you can make it. Cover, if you can:
   purpose, target audience, key pages/features, must-have integrations,
   branding constraints, success criteria. This pipeline runs unattended -
   nobody will ask you a follow-up question, so gaps become labeled
   assumptions instead.
4. Leave the schedule picker on "Manual only" for now - you'll add a
   schedule in Step 11.
5. Click **Create project**.

**Outcome:** the project appears in the Projects table with a "manual
only" schedule pill.

---

## Step 9 - Run it on demand (first real run)

Click **Run now** on your new project's row.

**What happens:** `run_manager.start_run` loads the project's team,
creates a `runs` row, and starts `pipeline.run_delivery_pipeline` in a
background thread. The first run for a fresh team also creates that
team's agents and the shared `org-standards` memory store (cached to
`data/agent_cache.json`, keyed per team, so later runs reuse them instead
of recreating them). If the team includes Smith (Developer) or either QA
role (Jack, Donald), this first run also broadens the shared sandbox
environment's network access in place (package manager registries +
GitHub) - a one-time, automatic step.

**Outcome:** the Run history table's new row shows `running`, then
updates automatically (polled every 4 seconds) to `success` or `failed`.
A first run typically takes 30 seconds to a few minutes - it's doing real
requirements analysis, architecture design, an outcome-rubric grading
pass, and two Google Docs + up to two Slack API calls.

If it fails, see Step 12.

---

## Step 10 - Verify the delivery

Once a run shows `success`:

1. **Local files** - check `outputs/<project-slug>/BRD.md` and
   `outputs/<project-slug>/Technical_Design_Document.md` (if the team
   includes Roger/Michael), `outputs/<project-slug>/site.zip` (if the
   team includes Smith - the run history row's Files column also gets a
   "Site (.zip)" link), and `outputs/<project-slug>/qa-artifacts.zip` (if
   the team includes Jack and/or Donald - a "QA (.zip)" link appears
   too). Unzip it: it contains `test-plan.md`, `test-cases.md`,
   `qa-report-local.md` (Jack), and `qa-report-cloud.md` (Donald, if he
   ran).
2. **Google Docs** - a "Delivery" folder should contain new documents
   titled after your project, for whichever of the BRD/TDD were produced.
3. **Slack** - your configured channel should have one or two short
   updates from the agent.
4. **Dashboard** - the agent cards now show real agent ids instead of
   "not created yet"; the run history row shows the estimated cost, which
   team ran it, and whether the outcome rubric reported `satisfied`.

**Outcome:** confirmation the whole chain - Claude, both memory stores,
the outcome rubric, and both MCP integrations - is working end to end
against your real accounts.

---

## Step 11 - Set up a schedule, and keep the app running unattended

On the project's row, open **Schedule** and pick a frequency:

- **Every day**, **Every week** (choose a weekday), or **Every month**
  (choose a day 1-31), plus a time - 12-hour (AM/PM) or 24-hour clock,
  hour/minute/second; or
- **Every N minutes** (the old "interval" option, still available as the
  simple advanced fallback), e.g. `60` for every 60 minutes.

No cron syntax anywhere - `scheduler.parse_schedule_form()` turns these
fields into the real APScheduler trigger. Click **Save schedule**.

**Outcome:** the Projects table shows the new schedule pill, and
`scheduler.add_or_update_job` registers a real APScheduler job -
confirmed in the smoke test by checking `scheduler.list_jobs()`.

**Important:** this scheduler runs *inside* the `python app.py` process.
For scheduled runs to actually fire, that process must be running at the
scheduled time. Options for keeping it running:

- **During testing:** just leave the terminal from Step 6 open.
- **On a machine you control:** run it under `tmux`/`screen`, or as a
  background process with `nohup python app.py &`.
- **On a server:** run it under a process manager (systemd, pm2,
  supervisord) configured to restart on crash and on boot.
- **If the app being down sometimes is unacceptable:** use Managed
  Agents' own native Scheduled Deployments feature instead of this app's
  scheduler - see README.md's "How scheduling actually works" section for
  the tradeoff.

Restarting the app is safe: `scheduler.sync_jobs_from_db()` rebuilds every
enabled schedule from SQLite on startup.

---

## Step 12 - Monitor run history and diagnose a failed run

The Run history table is your first stop. A `failed` row means the
background thread caught an exception - look at the terminal running
`python app.py` for the full traceback; `run_manager.py` also stores the
error message on the run record.

Common causes, and where to look:

| Symptom | Check |
|---|---|
| `400 invalid_request_error: anthropic-beta header cannot combine 'agent-memory-2026-07-22' with 'managed-agents-*' ...` | A real bug hit and fixed while building this app: the installed SDK auto-attaches its own beta for `memory_stores.*` calls, which conflicted with the explicit `managed-agents-2026-04-01` beta this code also passed on those calls. Already fixed in `pipeline.py` (no `betas=` on any `memory_stores.*` call) - if you see this again, check you're running the current version of `pipeline.py`, not an older copy. |
| `PipelineError` about a vault id | `.env`'s `GOOGLE_DOCS_VAULT_ID` / `SLACK_VAULT_ID` - must start with `vlt_`, not `sk-ant-` |
| `mcp_auth_failed` | The vault's MCP credential is missing, expired, or registered for a different URL - reconnect it in Claude Console |
| Run reaches `not_satisfied` and never clears | The outcome rubric and your project brief may be pulling in different directions - see README.md / `../labs/ClaudeMultiAgent_ManagedAgent/README.md` for how the rubric works, and consider a more complete brief |
| Run stays `running` forever | Likely a network stall inside the Anthropic SDK call - restart `python app.py` |

**A failed *local site start* is a separate thing from a failed run** -
see the "Local site" column, not "Error". The run itself can show
`success` (the pipeline produced real deliverables) while the site still
failed to boot locally, e.g. a missing build tool. That column shows a
plain-language suggested fix plus a **Retry** button that re-extracts and
restarts from the already-downloaded `site.zip` without spending API
credits on a new run - see `docs/multi-agent-teams.md`'s "Diagnosing and
retrying a failed local start" for how this works.

**This machine's default Node was changed for this reason**: the
system-wide Node install (v24, a very new "Current" release, not an LTS)
had no prebuilt binary for `better-sqlite3` and failed to even compile it
from source (a C++ standard mismatch between that Node version's headers
and the package's build config - installing more build tools doesn't fix
this). A portable Node 22 LTS was installed to `C:\vatsan\tools\node22`
(no admin rights needed, self-contained, no system install) and
prepended to this Windows user account's `PATH`, ahead of
`C:\Program Files\nodejs\` - so any project's plain `npm install`/
`npm run dev` now resolves to Node 22 automatically, which is much more
likely to have prebuilt binaries for whatever native packages a
Developer agent picks. This only affects `PATH` resolution order, not the
system-wide Node install - nothing else changes.

**How to actually see any of these:** a `failed` row's **Error** column in
the Run history table shows the stored message directly - click it to
expand the full text. You don't need to check the terminal unless the app
process itself crashed (rather than one run failing).

**Outcome:** you can tell, from the dashboard alone, whether a run
succeeded, and get a specific enough error to fix it without reading
source code.

---

## Step 13 - Customize org standards, an agent spec, or the model

- **Change your organization's default tech stack or tone:** edit
  `ORG_STANDARDS_STYLE` / `ORG_STANDARDS_TECH_DEFAULTS` in
  `agent_console/agentprompts/prompts.py`. This only affects *new* `org-standards`
  memory stores - an already-created one (check
  `data/agent_cache.json`) needs its memory files updated directly, or
  delete `data/agent_cache.json` to force recreation on the next run
  (this also recreates every team's agents).
- **Change a role's system prompt, description, skills, or tools label:**
  sidebar → **Agent Specs** → edit the role → **Save** (use **Validate**
  first for a quick sanity check). This is per-team-snapshot, not global:
  it only affects **teams created after the change**. A team created
  before the edit keeps using its already-provisioned agent's original
  prompt - either delete and recreate that team, or clear the matching
  `cache_key` from `data/agent_cache.json` to force just that one agent
  to be recreated on the next run.
- **Add or remove a role on an existing team:** Teams → the team's
  **Edit** page → check/uncheck roles → **Save**. If membership actually
  changed, the team's coordinator's cached agent id is cleared
  automatically, so it's recreated (with the updated delegation steps and
  roster) the next time that team runs - you don't need to touch
  `agent_cache.json` by hand for this specific case.
- **Change what counts as "done":** the rubric is now assembled per-team
  from `RUBRIC_BRD_SECTION` / `RUBRIC_TDD_SECTION` / `RUBRIC_SITE_SECTION`
  / `RUBRIC_QA_SECTION` in `agent_console/agentprompts/prompts.py`
  (`pipeline._build_rubric`), included only for the roles actually on the
  team. Edit those constants for org-wide
  rubric changes.
- **Use a cheaper/faster model:** set `MODEL=claude-haiku-4-5-20251001` in
  `.env` and restart the app. This only affects agents created *after*
  the change - delete `data/agent_cache.json` to force existing agents to
  be recreated with the new model.

**Outcome:** changes take effect on the next team created (agent-spec
edits) or the next run (org-standards/rubric/model changes, or after
clearing the agent cache for agent-level changes).

---

## Step 14 - Stop, restart, and reset safely

- **Stop:** `Ctrl+C` in the terminal running `python app.py`. In-flight
  runs' background threads are daemon threads and will be killed
  mid-request - they'll be left showing `running` forever in the
  database. There's no automatic cleanup for this; treat a `running` row
  from before a restart as abandoned.
- **Restart:** `python app.py` again. `scheduler.sync_jobs_from_db()`
  restores every enabled schedule automatically.
- **Full reset (keep code, wipe data):** stop the app, then delete
  `data/agent_console.sqlite3` and `data/agent_cache.json`. Next run
  recreates everything from scratch (new agents, new memory stores, empty
  run history).

**Outcome:** you know exactly what state survives a restart (schedules,
run history, agent ids) and what doesn't (in-flight runs).

---

## Step 15 - Troubleshooting quick reference

| Symptom | Likely cause / fix |
|---|---|
| `sqlite3.OperationalError: disk I/O error` on startup | `AGENT_CONSOLE_DB` points at a network share or sync folder that doesn't support SQLite's file locking - point it at a local path via the `AGENT_CONSOLE_DB` env var |
| Dashboard shows agents as "not created yet" forever | No run has reached agent creation yet - check the first run's error, if any |
| A run stays "running" indefinitely | Check the `python app.py` terminal for a traceback; restart if the SDK call hung |
| Scheduled run never fires | Confirm `python app.py` was running at the scheduled time (Step 11) |
| `PipelineError` about a vault id | See Step 12's table |
| Login fails with correct-looking credentials | Confirm `.env` actually has the values you think it does, and that you edited the copy in `agent_console/.env` (not `.env.example`) - `config.py` loads it automatically on startup, so a restart after editing `.env` is enough |
| VS Code's "Run" button uses the wrong Python / can't find `flask` | The interpreter picker (Step 0.5) isn't pointed at `venv` - check the bottom-left status bar in VS Code, or re-run **Python: Select Interpreter** |

---

## Quick-reference command summary

```bash
# One-time setup
cd agent_console
python3 -m venv venv
source venv/bin/activate        # Git Bash/Windows: source venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env

# Verify before spending API credits
python tests/test_smoke.py

# Run
python app.py
# then open http://localhost:5000 and log in
```

In VS Code, after Step 0's one-time setup, the last two commands are
equivalent to opening **Run and Debug** (`Ctrl+Shift+D`) and pressing `F5`
with **"Agent Console smoke test"** or **"Agent Console (app.py)"**
selected.
