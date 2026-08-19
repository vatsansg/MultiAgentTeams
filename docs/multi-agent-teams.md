# Multi-Agent Teams - design notes

This documents the feature added in the `Productv1` branch (see
`changerequestProductv1.md` at the repo root for the change-request log
and sequencing). It replaces the old fixed two-specialist roster
(`pipeline.COORDINATOR`/`pipeline.SPECIALISTS` Python constants) with a
DB-backed catalog of agent roles and named, reusable "teams," and adds a
full-stack Developer role, a broadened sandbox, and a non-cron scheduling
UI. Read this if you're extending any of that.

## Agent catalog (`agent_specs`)

One row per available role: `role_key` (stable identifier), `display_name`,
`role_title`, `description`, `system_prompt`, `handoff_instructions`
(non-coordinator roles only - see below), `skills` (JSON list), `tools_label`,
`is_coordinator`, `sequence` (delegation order). Seeded once on first
startup from `agent_specs_seed.py`'s `DEFAULT_AGENT_SPECS`, which imports
its prompt text from `../labs/shared/prompts.py`.

**`../labs/shared/prompts.py` is additive-only.** Its existing constants
(`DELIVERY_COORDINATOR_SYSTEM`, `BA_SPECIALIST_SYSTEM`,
`ARCHITECT_SPECIALIST_SYSTEM`, `DELIVERY_RUBRIC`, and others) are also
imported verbatim by
`../labs/ClaudeMultiAgent_ManagedAgent/ClaudeMultiAgent_ManagedAgent.ipynb`,
a separate project not versioned together with this one. None of them may
be renamed, removed, or edited in place. Every new prompt/handoff/rubric
constant this feature needed was added alongside the existing ones instead:
`DELIVERY_COORDINATOR_BOILERPLATE`, `BA_HANDOFF_INSTRUCTIONS`,
`ARCHITECT_HANDOFF_INSTRUCTIONS`, `DEVELOPER_SPECIALIST_SYSTEM`,
`DEVELOPER_HANDOFF_INSTRUCTIONS`, `RUBRIC_BRD_SECTION`,
`RUBRIC_TDD_SECTION`, `RUBRIC_SITE_SECTION`. `BA_SPECIALIST_SYSTEM` and
`ARCHITECT_SPECIALIST_SYSTEM` themselves are still reused unchanged - only
the coordinator prompt and rubric needed to become composable, since both
originals hardcode the old two-specialist shape.

Edit an existing role's text from the dashboard's **Agent Specs** page
(`role_key` and `is_coordinator` are not exposed there, so "exactly one
coordinator" holds by construction, not a runtime check). A "Validate"
button runs a fast, rule-based checklist (no LLM call) before you commit:
required fields non-empty, a coordinator prompt still contains the
`{{DELEGATION_STEPS}}`/`{{CLOSING_STEPS}}` placeholders, a non-coordinator
prompt mentions a `/workspace/` path, `skills` parses as a JSON list, a
soft 10,000-word cap on the system prompt.

**Adding a fifth role** (a QA agent is the obvious next one - deferred in
this pass, see the change request log): write its prompt in `prompts.py`,
add an entry to `agent_specs_seed.py`'s `DEFAULT_AGENT_SPECS`, and either
reseed (wipe the `agent_specs` table) or insert the row directly. If it
needs non-default tools, add a `_TOOLS_BY_ROLE["your_role_key"]` entry in
`pipeline.py` (see "Tool configs" below).

## Teams (`teams` + `team_members`)

Creating a team (**Teams** → **+ Create team**, or `db.create_team`)
snapshots every current `agent_specs` row into `team_members` at that
moment, in the same transaction. A team is always the *full* current
catalog - there's no picking a subset in this pass (matches how the
feature was specified: "Create Team will then add Delivery Lead agent and
other required agents..."). This means:

- Editing the catalog later never retroactively changes an
  already-created team. A team's `team_members` rows are its own
  permanent copy of the prompt/skills/tools it was created with - the
  same recreate-to-update semantics the platform itself already imposes
  on agents (RUNBOOK.md's Step 13), just now scoped per-team instead of
  globally.
- Each member gets a cache key of `team<team_id>_<role_key>`
  (`db.create_team`), used as the JSON key in `data/agent_cache.json`
  instead of the old flat `roger_id`/`michael_id`/`coordinator_id`. Two
  teams both containing a `business_analyst` never collide.
- `org_standards_id` and `environment_id` stay **global, shared across
  every team** - there's no reason to duplicate the sandbox environment
  or the org-standards memory store per team; only the agents themselves
  need per-team instances.

A team can't be deleted while any project still references it
(`db.team_in_use`) - the route flashes an error instead.

## Dynamic coordinator prompt assembly

The coordinator's `agent_specs`/`team_members` row stores a *template*
(`DELIVERY_COORDINATOR_BOILERPLATE`), not a finished prompt - it contains
the literal tokens `{{DELEGATION_STEPS}}` and `{{CLOSING_STEPS}}`.
`pipeline._build_coordinator_system(coordinator, specialists)` builds:

- **Delegation steps**: one numbered line per specialist, in `sequence`
  order, from that member's `handoff_instructions` (e.g. Roger's says
  what to hand him and to return when `/workspace/BRD.md` exists; Smith's
  says to pass him the TDD if it exists, otherwise the brief, and return
  when `/mnt/session/outputs/site.zip` exists).
- **Closing steps** (`_build_closing_steps`): always "copy every
  deliverable to `/mnt/session/outputs/`" and "post Slack updates" and
  "append to the project log," but conditionally includes "file to Google
  Docs" only if the team has a `business_analyst` or `solution_architect`,
  and a "don't deploy the website, `site.zip` is the deliverable" line
  only if it has a `developer`.

Substitution uses `str.replace`, not `str.format`/Jinja, deliberately -
an edited system prompt could contain a literal `{` (e.g. a JSON example)
that `.format()` would misinterpret as a field reference.

`pipeline._build_outcome_description` and `pipeline._build_rubric` follow
the same pattern: both are built per-team from the role set, not hardcoded
BA/Architect-specific text. `_build_rubric` includes `RUBRIC_BRD_SECTION`/
`RUBRIC_TDD_SECTION`/`RUBRIC_SITE_SECTION` only for roles actually present.

## Tool configs (not stored in SQLite)

Tool configs are nested dict/list shapes the SDK expects
(`agent_toolset_20260401`, `mcp_toolset`, `default_config`,
`permission_policy`, etc.) - not something the Agent Specs UI lets anyone
edit in this pass, and not a good fit for a flat TEXT column. They stay a
small Python mapping in `pipeline.py`:

```python
_SCOPED_WRITE_READ = [...]   # write+read only - BA, Architect
_DEV_TOOLSET = [{"type": "agent_toolset_20260401"}]  # full, unscoped, incl. bash - Developer
_TOOLS_BY_ROLE = {
    "business_analyst": _SCOPED_WRITE_READ,
    "solution_architect": _SCOPED_WRITE_READ,
    "developer": _DEV_TOOLSET,
}
```

A role with no entry falls back to `_SCOPED_WRITE_READ`.

## The Developer role ("Smith") and the broadened sandbox

Smith needs to install dependencies and run a local dev/build command, so
he gets the full unscoped toolset (bash included) instead of the
BA/Architect's scoped write/read-only set.

The shared sandbox environment's networking was originally locked to just
`docs.googleapis.com`/`www.googleapis.com` with `allow_package_managers:
False`. Adding the Developer role required broadening it. Decision made
(with the user, not unilaterally): **one shared environment for every
team**, broadened once, rather than a second Developer-only environment -
simpler, and the extra `allowed_hosts` (npm/PyPI/Maven/NuGet registries,
GitHub) are harmless for teams that never use them.

`pipeline._get_or_create_environment` handles this with a version marker
(`_ENVIRONMENT_CONFIG_VERSION`) so an already-cached environment gets
broadened **in place**, exactly once, automatically, the first time any
run happens after this change - via `client.beta.environments.update()`
(confirmed present in the installed SDK, taking the same `config` shape
as `create()`), not a delete-and-recreate.

### Website delivery: `site.zip`, not individual files

Unlike the BA/Architect's single markdown file each, Smith's output is a
multi-file website. His system prompt instructs him to zip
`/workspace/site/` into `/workspace/site.zip` (so unzipping reproduces the
`site/` folder exactly) and copy it to `/mnt/session/outputs/site.zip`.
`pipeline.py`'s `wanted` dict (which filters which session files get
downloaded) includes `"site.zip"` only when the team has a `developer`.
The dashboard serves it as-is (`application/zip`, no server-side unzip -
matches how `brd_path`/`tdd_path` are already just stored file paths, and
a user running the site locally has to unzip it themselves regardless).

### The "ralph loop" clarification

The original request asked for Smith to "use ralph loop plugins for
better UI design." **Ralph Loop is a Claude Code plugin** - an
iterate-build-critique-repeat technique that runs inside a Claude Code
session. It is **not** something a Managed Agents platform session (which
is what `pipeline.py` drives) can invoke - Managed Agents sessions don't
have access to Claude Code plugins; they're a different product surface
entirely.

What Smith actually has instead is an **emulated, in-prompt version of
the same idea** (agreed with the user as the resolution to this gap): his
system prompt (`DEVELOPER_SPECIALIST_SYSTEM` in `prompts.py`) contains an
explicit "SELF-REVIEW LOOP" section - build, self-critique against a
checklist (every page/feature present and working, README's own run
steps actually work, responsive at both mobile and desktop widths, no
obviously broken state, coherent visual design), revise if needed, repeat
up to 4 passes. This is a prompt-engineering pattern, not a tool
invocation, and has **no relationship to the actual `ralph-loop` Claude
Code plugin**. This is written down here specifically so a future
maintainer doesn't go looking for where the plugin is "wired in" - it
isn't, by design, because it can't be from inside this pipeline.

## Scheduling model

Replaces the old two free-text fields (`cron_expression`,
`interval_minutes`) with a structured picker: frequency (daily / weekly
on a chosen weekday / monthly on a chosen day-of-month / "every N
minutes"), a 12-/24-hour clock toggle, and hour/minute/second.

- `scheduler.parse_schedule_form(form)` turns the dashboard's POST fields
  into a schedule dict (`{"frequency":, "hour":, "minute":, "second":,
  ["weekday":|"month_day":|"interval_minutes":], "summary":}`), doing the
  12h→24h conversion server-side (hour is always stored in 24-hour
  canonical form in the DB regardless of which clock mode was used to
  enter it - the AM/PM toggle is purely a UI presentation concern).
- `scheduler.add_or_update_job(project_id, schedule)` builds a real
  APScheduler `CronTrigger`/`IntervalTrigger` directly from that dict -
  not `CronTrigger.from_crontab()`, since a 5-field crontab string has no
  seconds field and the feature explicitly asked for second-level
  control. Weekday is matched by **name** (`'mon'..'sun'`, confirmed
  `CronTrigger` accepts this directly), not by number, to sidestep any
  Sunday-vs-Monday-first numbering-convention ambiguity entirely.
- `projects.cron_expression` stays in the schema for backward
  compatibility but is no longer written by the new UI;
  `interval_minutes` is still written and used as the "every N minutes"
  advanced fallback option, per the user's explicit request to keep it.
  `schedule_summary` stores a human-readable confirmation string (e.g.
  "Every Wednesday at 6:30:15 PM") shown next to the picker.

## What's deferred (see `changerequestProductv1.md`'s "Recommended further
improvements" for the full list)

Team-membership editing after creation, a QA agent role, a deployment
agent (Smith's output is local-run-only by design), agent-spec
versioning/audit trail, automated testing of Smith's actually-built
website output (this pass only tests that `site_path` plumbing works, not
runtime correctness of AI-generated application code), rate-limiting the
now-broader sandbox network egress, multi-user/team ownership, and full
`agent_specs` CRUD (new roles / deleting roles / reassigning coordinator
status through the UI).
