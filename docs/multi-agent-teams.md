# Multi-Agent Teams - design notes

This documents the feature added in the `Productv1` branch (see
`changerequestProductv1.md` at the repo root for the change-request log
and sequencing). It replaces the old fixed two-specialist roster
(`pipeline.COORDINATOR`/`pipeline.SPECIALISTS` Python constants) with a
DB-backed catalog of agent roles and named, reusable "teams" with
choosable membership, and adds a full-stack Developer role, two QA Tester
roles, a broadened sandbox, and a non-cron scheduling UI. Read this if
you're extending any of that.

## Agent catalog (`agent_specs`)

One row per available role: `role_key` (stable identifier), `display_name`,
`role_title`, `description`, `system_prompt`, `handoff_instructions`
(non-coordinator roles only - see below), `skills` (JSON list), `tools_label`,
`is_coordinator`, `sequence` (delegation order). Currently seeded:
`delivery_lead` (coordinator), `business_analyst` (Roger),
`solution_architect` (Michael), `developer` (Smith), `qa_local` (Jack),
`qa_cloud` (Donald).

Seeding is **idempotent per `role_key`, and runs on every startup**
(`db._seed_default_agent_specs()`), not just when the table is empty: for
each entry in `agent_specs_seed.py`'s `DEFAULT_AGENT_SPECS`, it inserts
only if that `role_key` isn't already present. This is how Jack and
Donald were added after Smith had already shipped - adding their entries
to `DEFAULT_AGENT_SPECS` was enough; the next app startup backfilled them
into the existing database automatically, no wipe, no migration script.
**Adding a sixth/seventh role later follows the same path**: write its
prompt/handoff constants in `prompts.py` (additive only, see below), add
one entry to `DEFAULT_AGENT_SPECS`, and if it needs non-default tools, add
a `_TOOLS_BY_ROLE["your_role_key"]` entry in `pipeline.py` (see "Tool
configs" below) - then just restart the app.

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
`RUBRIC_TDD_SECTION`, `RUBRIC_SITE_SECTION`, `QA_LOCAL_SPECIALIST_SYSTEM`,
`QA_LOCAL_HANDOFF_INSTRUCTIONS`, `QA_CLOUD_SPECIALIST_SYSTEM`,
`QA_CLOUD_HANDOFF_INSTRUCTIONS`, `RUBRIC_QA_SECTION`. `BA_SPECIALIST_SYSTEM`
and `ARCHITECT_SPECIALIST_SYSTEM` themselves are still reused unchanged -
only the coordinator prompt and rubric needed to become composable, since
both originals hardcode the old two-specialist shape.

Edit an existing role's text from the dashboard's **Agent Specs** page
(`role_key` and `is_coordinator` are not exposed there, so "exactly one
coordinator" holds by construction, not a runtime check). A "Validate"
button runs a fast, rule-based checklist (no LLM call) before you commit:
required fields non-empty, a coordinator prompt still contains the
`{{DELEGATION_STEPS}}`/`{{CLOSING_STEPS}}` placeholders, a non-coordinator
prompt mentions a `/workspace/` path, `skills` parses as a JSON list, a
soft 10,000-word cap on the system prompt.

## Teams (`teams` + `team_members`) - dynamic membership

Creating a team (**Teams** → **+ Create team**) shows a checkbox per
catalog role - Delivery Lead has no checkbox and is always included as
coordinator; every specialist checkbox defaults to checked (so the common
case, "give me everyone," is zero extra clicks) but can be unchecked.
`db.create_team(name, description, spec_ids)` snapshots only the chosen
`agent_specs` rows (plus the coordinator, forced) into `team_members`, in
one transaction.

**Editing membership after creation**: Teams → a team's **Edit** button →
same checkbox list, pre-checked to match current membership → **Save**.
`db.update_team_members(team_id, spec_ids)` reconciles the diff (deletes
`team_members` rows for unchecked non-coordinator roles, inserts new ones
for newly-checked roles) and returns whether anything actually changed.
**If it did change**, the route calls
`pipeline.invalidate_cached_agent(coordinator_cache_key)` - dropping just
the coordinator's entry from `data/agent_cache.json` - because the
coordinator's `multiagent.agents` roster and its dynamically-assembled
system prompt (see below) are both built **only at agent-creation time**;
editing `team_members` alone doesn't retroactively rewrite an
already-provisioned coordinator's platform-side config. Clearing its
cache_key forces `_get_or_create_agents` to recreate it, correctly, on
the team's next run. A newly-added specialist's own agent is created
normally in the same run (its cache_key was never cached, since it's
new); a removed specialist's platform agent is simply left unreferenced
(no delete endpoint exists anyway - see teardown below).

This means:

- Editing the **catalog** (Agent Specs page) later never retroactively
  changes an already-created team - separate from the membership-editing
  above, which changes *which roles* a team has, not what an existing
  member's prompt says. A team's `team_members` rows are their own
  permanent copy of the prompt/skills/tools they were created with - the
  same recreate-to-update semantics the platform itself already imposes
  on agents (RUNBOOK.md's Step 13), just now scoped per-team instead of
  globally.
- Each member gets a cache key of `team<team_id>_<role_key>`, used as the
  JSON key in `data/agent_cache.json` instead of the old flat
  `roger_id`/`michael_id`/`coordinator_id`. Two teams both containing a
  `business_analyst` never collide.
- `org_standards_id` and `environment_id` stay **global, shared across
  every team** - there's no reason to duplicate the sandbox environment
  or the org-standards memory store per team; only the agents themselves
  need per-team instances.

A team can't be deleted while any project still references it
(`db.team_in_use`) - the route flashes an error instead.

## Per-team agent renaming

Every role can be given its own `display_name` **for a given team**,
independent of the catalog's default and independent of what the same
role is named on any other team. Both the Create Team and Edit Team forms
render a `name_for_<agent_spec_id>` text input per role, pre-filled with
the catalog default (create) or the team's current name (edit).
`db.create_team`/`db.update_team_members` both take an optional
`custom_names: {agent_spec_id: display_name}` dict.

**The only rule**: names must be unique *within* a team
(`db._validate_unique_names`, case-insensitive and trimmed - raises
`DuplicateTeamMemberNameError`, caught by the dashboard route and
flashed). The same name is completely fine reused across two different
teams, or reused as a different role's name than in the catalog.

**Why a rename isn't just a `display_name` column update**: several
system prompts refer to the agent by name in prose, not just the
`display_name` field - e.g. `BA_SPECIALIST_SYSTEM` opens "You are Roger,
a Business Analyst...", and `QA_CLOUD_SPECIALIST_SYSTEM` (Donald's own
prompt) says "...Jack's artifacts from the local pass" - a
**cross-reference** to a *different* team member, not itself. If only the
`display_name` column changed, Roger's own agent would still introduce
itself as "Roger" in its actual behavior, and Donald's prompt would still
say "Jack" even after Jack was renamed.

`db._apply_name_renames(text, rename_map)` fixes this: given a
`{old_catalog_name: new_team_name}` map, it word-boundary-substitutes
every occurrence in a role's `system_prompt` and `handoff_instructions` -
covering both self-references and cross-references, since the
replacement function runs uniformly whether or not the text belongs to
the renamed role itself. `create_team` builds this map from every
included role's catalog name → its chosen name; `update_team_members`
builds it as a **full current-state map** (every kept role's catalog
name → its final name *right now*, not just what changed in this
specific edit) and always re-derives every kept member's
`system_prompt`/`handoff_instructions` from the **pristine `agent_specs`
text**, never from a team member's own already-substituted row. This
matters for correctness across repeated edits: if it instead patched the
already-stored (possibly already-renamed) text incrementally, a rename
from an earlier edit that isn't touched by a later edit would either
silently revert or double-substitute. Re-deriving from pristine text with
the complete current name-mapping every time avoids both failure modes -
verified in `tests/test_smoke.py` by renaming Jack→Alice, then Alice→
Charlie, then (in a third, unrelated edit) renaming only Smith, and
confirming Donald's prompt still correctly says "Charlie."

**Invalidation scope on edit**: `update_team_members` returns one of
`"none"` / `"coordinator"` / `"all"`. Adding or removing a role without
any rename only needs the **coordinator** recreated (its
`multiagent.agents` roster and delegation steps are stale, nothing else
is). But **any** rename returns `"all"` - the dashboard route then calls
`pipeline.invalidate_cached_agent()` on *every* member's `cache_key`, not
just the renamed one or the coordinator. This is deliberately
conservative: a rename could be a cross-referenced name in an arbitrary
other member's prompt (today, only Donald cross-references Jack, but
nothing enforces that staying true as more roles are added), and
computing the exact minimal blast radius per rename is more complexity
than it's worth for an action that isn't a hot path. Recreating a whole
team's agents costs a few API calls on the team's next run; shipping a
stale name reference costs correctness.

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
  a "don't deploy the website, `site.zip` is the deliverable" line only
  if it has a `developer`, and a "QA artifacts are a required deliverable"
  line only if it has `qa_local` or `qa_cloud`.

Substitution uses `str.replace`, not `str.format`/Jinja, deliberately -
an edited system prompt could contain a literal `{` (e.g. a JSON example)
that `.format()` would misinterpret as a field reference.

`pipeline._build_outcome_description` and `pipeline._build_rubric` follow
the same pattern: both are built per-team from the role set, not hardcoded
BA/Architect-specific text. `_build_rubric` includes `RUBRIC_BRD_SECTION`/
`RUBRIC_TDD_SECTION`/`RUBRIC_SITE_SECTION`/`RUBRIC_QA_SECTION` only for
roles actually present.

## Tool configs (not stored in SQLite)

Tool configs are nested dict/list shapes the SDK expects
(`agent_toolset_20260401`, `mcp_toolset`, `default_config`,
`permission_policy`, etc.) - not something the Agent Specs UI lets anyone
edit in this pass, and not a good fit for a flat TEXT column. They stay a
small Python mapping in `pipeline.py`:

```python
_SCOPED_WRITE_READ = [...]   # write+read only - BA, Architect
_DEV_TOOLSET = [{"type": "agent_toolset_20260401"}]  # full, unscoped, incl. bash
_TOOLS_BY_ROLE = {
    "business_analyst": _SCOPED_WRITE_READ,
    "solution_architect": _SCOPED_WRITE_READ,
    "developer": _DEV_TOOLSET,
    "qa_local": _DEV_TOOLSET,
    "qa_cloud": _DEV_TOOLSET,
}
```

A role with no entry falls back to `_SCOPED_WRITE_READ`.

## The Developer role ("Smith") and the broadened sandbox

Smith needs to install dependencies and run a local dev/build command, so
he gets the full unscoped toolset (bash included) instead of the
BA/Architect's scoped write/read-only set. Jack and Donald (QA) need the
same for the same reason - they install and start the site themselves to
actually execute test cases against it, not just read source.

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

## The QA roles ("Jack" and "Donald")

Two sequential QA roles, `qa_local` (Jack, sequence 4) and `qa_cloud`
(Donald, sequence 5) - always after `developer` (sequence 3) in the
delegation order, since both read whatever Smith built.

- **Jack** reviews the BRD/TDD/brief against project scope, writes a test
  plan and test cases under `/workspace/qa/`, and - if `/workspace/site/`
  exists - installs and starts it (bash) and actually executes the test
  cases, recording pass/fail and any defects in
  `/workspace/qa/qa-report-local.md`. If no site was built (a
  QA-only-team edge case), he still produces the test plan/cases from the
  docs alone and says plainly that nothing was executed.
- **Donald** reads Jack's artifacts (or does the same review from scratch
  if Jack isn't on the team) and re-runs the same test cases himself,
  recording whether he reproduces Jack's results and adding any test
  cases he thinks the local pass missed, in
  `/workspace/qa/qa-report-cloud.md`.
- **Both** zip `/workspace/qa/` into `/workspace/qa-artifacts.zip` and
  copy it to `/mnt/session/outputs/qa-artifacts.zip` at the end of their
  own turn (not just the last one) - so whichever QA role runs last
  (Donald, if both are on the team; Jack, if he's alone) always leaves
  the complete, current set of QA files as the final deliverable,
  regardless of team composition, without either prompt needing to know
  whether the other agent is also on the team.

**Why "cloud" doesn't mean a real deployment here**: the request was for
Donald to test "in the Cloud test environment" as a second environment
distinct from Jack's "local dev environment." There is no deployment
agent in this pass (explicitly out of scope, per the user), so there is
no separately hosted copy of the site to point Donald at. Being honest
about that constraint, Donald's system prompt (`QA_CLOUD_SPECIALIST_SYSTEM`
in `prompts.py`) explicitly frames "cloud" as: the same Managed Agents
sandbox this whole session already runs in (its `config.type` is
literally `"cloud"` - see `pipeline._get_or_create_environment`), treated
as a second, independent test pass rather than a literal second
deployment target. If/when a deployment agent is added later, Donald's
prompt is the natural place to point him at whatever gets deployed
instead of re-running the same `/workspace/site/`.

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

A deployment agent (Smith's output is local-run-only by design, and
Donald's "cloud" testing is explicitly the existing sandbox, not a real
deployment - see above), agent-spec versioning/audit trail, automated
testing of Smith's actually-built website output and Jack/Donald's actual
QA findings (this pass only tests that the plumbing - `site_path`,
`qa_artifacts_path` - works end to end, not the runtime correctness of
AI-generated application code or AI-generated test results),
rate-limiting the now-broader sandbox network egress, multi-user/team
ownership, and full
`agent_specs` CRUD (new roles / deleting roles / reassigning coordinator
status through the UI).
