# Change Request Log - Productv1

Branch: `Productv1` (from `master`, pushed to
https://github.com/vatsansg/MultiAgentTeams)

This log records every change in this pass as a numbered Change Request,
in the order they were implemented. Each CR is independently buildable
and testable before the next one starts - `tests/test_smoke.py` passed
after each. Full design detail lives in `docs/multi-agent-teams.md`; this
file is the sequencing/scope record.

## CR-1: Agent spec catalog + Teams data model

New tables `agent_specs`, `teams`, `team_members`; new `projects.team_id`
and `schedule_*` columns; new `runs.team_name`/`site_path` columns; a
defensive `_add_column_if_missing` migration helper (db.py had none);
`agent_specs_seed.py` seeding the default 4-role catalog (Delivery Lead,
Roger, Michael, Smith) on first startup; additive-only new constants in
`../labs/shared/prompts.py` (nothing existing renamed/removed/edited - see
`docs/multi-agent-teams.md` for why). **Files:** `db.py`,
`agent_specs_seed.py` (new), `../labs/shared/prompts.py`.

## CR-2: Team-driven pipeline refactor

Replaced `pipeline.py`'s hardcoded `COORDINATOR`/`SPECIALISTS` module
constants with a `team` bundle parameter (`db.get_team_with_members`)
consumed by `_get_or_create_agents`, `run_delivery_pipeline`, and
`teardown_platform_resources`. Added dynamic coordinator system-prompt
assembly (`_build_coordinator_system`/`_build_closing_steps`) and dynamic
outcome-description/rubric building (`_build_outcome_description`/
`_build_rubric`), both conditional on team composition. Threaded `team`
through `run_manager.start_run` (including a readable-error path for a
project with no team assigned) and `dashboard.py`'s `/teardown` route
(now builds a generalized `agent_roster` from `db.list_all_team_members()`
instead of the old fixed roster). **Files:** `pipeline.py`,
`run_manager.py`, `dashboard.py` (teardown route only).

## CR-3: Developer (Smith) role, sandbox environment broadening, website delivery

Added `DEVELOPER_SPECIALIST_SYSTEM`/`DEVELOPER_HANDOFF_INSTRUCTIONS` to
`prompts.py` (full self-review-loop system prompt - see "ralph loop"
clarification in `docs/multi-agent-teams.md`). Added `_TOOLS_BY_ROLE`/
`_DEV_TOOLSET` (Smith gets the full unscoped toolset, not the BA/
Architect's scoped write/read-only set) and broadened the one shared
sandbox environment's `allowed_hosts`/`allow_package_managers` in place,
version-gated so it happens automatically exactly once
(`client.beta.environments.update()`, confirmed present in the installed
SDK - corrected from an initial delete-and-recreate assumption). Added
`site.zip` to the conditional file-download `wanted` dict and
`PipelineResult.site_path`; added the `/runs/<id>/files/site` download
route (`application/zip`). **Files:** `../labs/shared/prompts.py`,
`pipeline.py`, `dashboard.py`.

## CR-4: Sidebar navigation shell + read-only Agents catalog page

Restructured `base.html` into a two-column shell (`.app-shell` /
`.sidebar` / `.container`) for logged-in pages only (login page keeps the
old single-column layout); fixed a Jinja "block defined twice" error by
using `{{ self.content() }}` in the logged-out branch instead of a second
`{% block content %}`. Added sidebar/select/skill-chip/schedule-picker CSS
to `style.css`, plus a sub-860px responsive collapse to a horizontal tab
strip. Added `GET /agents` and `templates/agents.html` (read-only catalog
cards with skill chips). **Files:** `templates/base.html`,
`static/style.css`, `dashboard.py`, `templates/agents.html` (new).

## CR-5: Teams pages + danger-zone data source update

Added `GET /teams`, `GET+POST /teams/new`, `POST /teams/<id>/delete`
(blocked while a project references the team) and their templates.
Updated the dashboard's danger-zone summary and teardown-confirmation
modal to iterate every team's members instead of the old fixed
`coordinator`/`specialists` globals. **Files:** `dashboard.py`,
`templates/teams.html` (new), `templates/team_new.html` (new),
`templates/dashboard.html` (danger-zone section).

## CR-6: Agent Specs edit + rule-based validation

Added `GET /agent-specs`, `GET+POST /agent-specs/<id>/edit`, a rule-based
(no LLM call) `_validate_agent_spec` checklist, and a dual "Validate"/
"Save" submit on the same form. `role_key`/`is_coordinator` are
intentionally not editable fields. **Files:** `dashboard.py`,
`templates/agent_specs.html` (new), `templates/agent_spec_edit.html` (new).

## CR-7: Dashboard team selection, dynamic org chart, run history Team column

Rewrote `dashboard.py`'s `index()`/`create_project()` for team-first
selection (`?team_id=` query param, defaulting to the first team); the
org-chart section now splits `selected_team.members` by `is_coordinator`
instead of reading fixed globals; the new-project form's Team `<select>`
is the first required field; run history gained a `Team` column
(`colspan` 9→10). **Files:** `dashboard.py`, `templates/dashboard.html`.

## CR-8: Structured scheduling UI (replaces free-text cron/interval fields)

Replaced the two free-text fields with a frequency picker (daily / weekly
+ weekday / monthly + day-of-month / "every N minutes"), 12h/24h clock
toggle, and hour/minute/second inputs, in both the new-project form and
the per-project schedule-edit form (shared Jinja macro). Rewrote
`scheduler.py`: `parse_schedule_form` (structured POST fields → schedule
dict, with server-side 12h→24h conversion) and `add_or_update_job` (builds
`CronTrigger`/`IntervalTrigger` directly from the dict - weekday matched
by name, not number, since `CronTrigger` accepts names directly; seconds
supported natively, unlike the old `from_crontab()` approach). Added base
`<select>` CSS (didn't exist before). **Files:** `scheduler.py`,
`dashboard.py`, `templates/dashboard.html`, `static/style.css`.

## CR-9: Documentation + QA test coverage

Rewrote README.md's roster section into "Teams and the agent catalog,"
updated the scheduling section, files table, and architecture paragraph;
updated RUNBOOK.md Steps 7-13 (added Step 7b: create your first team);
added `docs/multi-agent-teams.md` (full design notes, including the
explicit "ralph loop is a Claude Code plugin, not something invocable
here - Smith gets an emulated in-prompt version instead" clarification);
this file. Extended `tests/test_smoke.py` in place (still no pytest,
plain `assert`/`print`, same `FakeAnthropicClient`/swappable-pipeline-fn
pattern) with 36 total checks covering: catalog seeding, team snapshot +
cache-key collision prevention, project creation requiring a team, the
no-team-assigned failure path, dynamic org-chart rendering, the
`site.zip` download route, structured-schedule form parsing and the
resulting `CronTrigger`/`IntervalTrigger` field values (daily/weekly/
monthly/interval, not just job presence), agent-spec validate/save
(including the coordinator-placeholder rule), team-deletion-while-in-use
guard, and the generalized teardown roster. **Files:** `README.md`,
`RUNBOOK.md`, `docs/multi-agent-teams.md` (new), `tests/test_smoke.py`,
this file.

## CR-10: Dynamic team composition (choose roles at creation + edit membership later)

Originally CR-1 through CR-9 shipped with "a team always gets the full
current catalog" (matching a literal reading of the initial request).
Follow-up clarification: teams need a real picker. Added a `spec_ids`
parameter to `db.create_team` (checkbox list on the Create Team form,
defaulting to all checked; the coordinator has no checkbox and is always
force-included) and a new `db.update_team_members(team_id, spec_ids)` +
`GET/POST /teams/<id>/edit` + `templates/team_edit.html` for changing an
existing team's roles afterward. Since the coordinator's `multiagent`
roster and dynamic system prompt are only assembled at agent-creation
time, a membership change that's real (not a no-op resubmit) calls the
new `pipeline.invalidate_cached_agent()` on just the team's coordinator
cache_key, forcing it to be recreated with the updated roster on the next
run - without needing to touch the team's other, unaffected specialist
agents. **Files:** `db.py`, `pipeline.py`, `dashboard.py`,
`templates/team_new.html`, `templates/team_edit.html` (new),
`templates/teams.html`, `templates/base.html` (sidebar active-link),
`tests/test_smoke.py`.

## CR-11: Two QA Tester roles (Jack - local, Donald - cloud)

Added `qa_local` (Jack) and `qa_cloud` (Donald) to the agent catalog,
treated identically to Business Analyst/Solution Architect/Developer -
same `agent_specs` shape, same team-membership picker, same dynamic
coordinator delegation/rubric/outcome-description wiring, same teardown
roster (already fully dynamic from CR-2, so Jack/Donald required **zero**
teardown code changes - see `docs/multi-agent-teams.md`). Jack reviews
BRD/TDD/scope and writes+executes a test plan/test cases against
Smith's site running locally; Donald builds on Jack's artifacts and
re-validates in the shared cloud sandbox environment (see
`docs/multi-agent-teams.md`'s explicit note on what "cloud" honestly
means here, absent a deployment agent). Both package their output as
`qa-artifacts.zip`. Added `PipelineResult.qa_artifacts_path`,
`runs.qa_artifacts_path` column, the `/runs/<id>/files/qa` download
route, and a "QA (.zip)" link in run history's Files column.

Also switched `agent_specs` seeding from "once, only if the table is
empty" to **idempotent per-`role_key`, every startup** - this is what let
Jack and Donald backfill into the already-existing local database without
a wipe, and is now the supported way to add any future role. **Files:**
`../labs/shared/prompts.py`, `agent_specs_seed.py`, `db.py`,
`pipeline.py`, `run_manager.py`, `dashboard.py`,
`templates/dashboard.html`, `tests/test_smoke.py`, `README.md`,
`RUNBOOK.md`, `docs/multi-agent-teams.md`.

## CR-12: Per-team agent renaming (unique within a team)

Each role can now be given its own display name for a given team, at
creation or via Edit - a `name_for_<agent_spec_id>` text field per role
on both forms, defaulting to the catalog name. The only rule: names must
be unique *within* a team (`db._validate_unique_names`, case-insensitive
- raises the new `DuplicateTeamMemberNameError`), never globally.

Renaming isn't just a `display_name` column write: several system
prompts refer to their agent by name in prose (`BA_SPECIALIST_SYSTEM`
opens "You are Roger..."), and one prompt cross-references a *different*
agent by name (Donald's own system prompt says "...Jack's artifacts").
New `db._apply_name_renames()` word-boundary-substitutes a role's
original catalog name for its team-specific name across every included
member's `system_prompt`/`handoff_instructions` - both self- and
cross-references - always re-derived from the pristine `agent_specs` text
(never from an already-substituted `team_members` row), so cumulative
renames across multiple edits never drift or silently revert (covered by
a dedicated smoke-test sequence: rename Jack→Alice→Charlie, then an
unrelated edit renaming only Smith, confirming Donald's prompt still says
"Charlie" afterward).

`db.update_team_members` now returns `"none"`/`"coordinator"`/`"all"`
instead of a bool - membership-only changes (add/remove a role) still
only invalidate the coordinator's cached agent, but **any** rename
invalidates **every** member's cached agent (deliberately conservative -
a rename could be cross-referenced in any other member's prompt text, and
computing the exact minimal blast radius isn't worth the complexity for
a low-frequency admin action). **Files:** `db.py`, `dashboard.py`,
`templates/team_new.html`, `templates/team_edit.html`,
`tests/test_smoke.py`, `README.md`, `docs/multi-agent-teams.md`.

---

## Recommended further improvements

Scoped out of this pass deliberately - listed here so they're a decision,
not an oversight:

- **A deployment agent** - Smith's website deliverable is local-run-only
  by design, and Donald's "cloud" QA pass is explicitly the existing
  Managed Agents sandbox, not a real hosted deployment (see
  `docs/multi-agent-teams.md`). Deploying anywhere is explicitly out of
  scope for this pass, per the user's own instruction.
- **Agent-spec versioning/audit trail** - who changed a spec's prompt,
  when, and a diff view. Today an edit just overwrites the row.
- **Automated testing of Smith's actually-built website output, and of
  Jack/Donald's actual QA findings** - this pass only verifies the
  pipeline plumbing (`site_path`/`qa_artifacts_path` flow through
  correctly end to end); it does not execute or evaluate the runtime
  correctness of AI-generated application code or AI-generated test
  results.
- **Rate-limiting/monitoring the now-broader sandbox network egress** -
  `allow_package_managers: True` plus the npm/PyPI/Maven/NuGet/GitHub
  allowlist is unconditional once any team includes Developer or QA.
- **Multi-user/team ownership** - still a single shared login; there's no
  "who created this team/project" tracking.
- **Full `agent_specs` CRUD** - creating brand-new roles or deleting/
  reassigning `is_coordinator` through the UI (new roles today are added
  by editing `agent_specs_seed.py` and restarting - see
  `docs/multi-agent-teams.md`). This pass only lets the UI edit an
  existing seeded role's text/skills/prompt fields.
- **A dedicated QA-to-Developer feedback loop** - today Jack/Donald report
  defects in `qa-report-*.md`, but nothing routes a defect back to Smith
  for a fix within the same run; a human (or a future run) has to act on
  the QA report manually.
