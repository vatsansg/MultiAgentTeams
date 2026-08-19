"""Wraps the ClaudeMultiAgent_ManagedAgent capstone-style Managed Agents
pipeline as a reusable Python function the Flask app can call in a
background thread.

Originally the same fixed logic as
labs/ClaudeMultiAgent_ManagedAgent/ClaudeMultiAgent_ManagedAgent.ipynb -
one hardcoded coordinator (Delivery Lead) delegating to two hardcoded
specialists (Roger, Michael). That's now generalized: the caller passes a
"team" bundle (see db.get_team_with_members) describing which agents exist
and their delegation order, built from the DB-backed agent_specs catalog +
per-team snapshot (agent_console/db.py, agent_console/agent_specs_seed.py).
Two memory stores still ride along, an outcome rubric still grades the
result (now assembled per-team - see _build_rubric), and the coordinator
still files documents to Google Docs / posts Slack updates when the team
includes the roles that produce them - restructured so it can run
headless, triggered by a button click or a scheduler instead of a human
stepping through notebook cells.

The sandbox environment (shared globally across all teams), each team's
agents, and the org-standards memory store (also shared globally) are all
created once and cached to disk (config.AGENT_CACHE_PATH), rather than
recreated on every run. Each team's agents are cached under a
team-namespaced key (see db.create_team's cache_key) so two teams' same
role (e.g. two "business_analyst"s) never collide.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import config

sys.path.insert(0, config.LABS_SHARED_DIR)
from prompts import (  # noqa: E402  (import after sys.path insert, matches notebook convention)
    ORG_STANDARDS_STYLE,
    ORG_STANDARDS_TECH_DEFAULTS,
    RUBRIC_BRD_SECTION,
    RUBRIC_TDD_SECTION,
    RUBRIC_SITE_SECTION,
)
from cost_meter import estimate_session_cost  # noqa: E402


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "project"


@dataclass
class PipelineResult:
    session_id: str
    satisfied: bool
    brd_path: Optional[str] = None
    tdd_path: Optional[str] = None
    site_path: Optional[str] = None
    google_doc_links: list = field(default_factory=list)
    cost_usd: Optional[float] = None
    events_log: list = field(default_factory=list)


class PipelineError(RuntimeError):
    """Raised for any configuration or run failure, with a human-readable message."""


class PipelineStopped(PipelineError):
    """Raised when a `should_stop` callback asks the run to end early (the
    dashboard's Stop button - see run_manager.request_stop). Caught
    separately from PipelineError in run_manager so the run is recorded as
    user-stopped rather than as a generic failure."""


def get_live_cost(session_id: str) -> Optional[float]:
    """Fetch a fresh cost estimate for a session that might still be
    in-flight - used by the dashboard to show cost updating live while a
    run's status is still 'running', not just once it's finished. Returns
    None on any failure (no API key configured, transient network error,
    session not found yet) - the caller just keeps showing the last known
    value in that case rather than erroring the whole page.
    """
    if not config.ANTHROPIC_API_KEY or not session_id:
        return None
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        session = client.beta.sessions.retrieve(session_id, betas=config.BETAS)
        cost = estimate_session_cost(session, config.MODEL)
        return cost["total_cost"]
    except Exception:
        return None


# --------------------------------------------------------------------------
# Vault / MCP resolution helpers - identical in spirit to ClaudeMultiAgent_ManagedAgent.ipynb's
# Step 1 cell, factored out so both the notebook logic and this module stay
# in sync conceptually. Kept here rather than imported from the notebook
# since notebooks aren't importable modules.
# --------------------------------------------------------------------------


def _validate_vault_id(vault_id: str, env_var: str, provider: str):
    if not vault_id or vault_id.startswith("vlt_REPLACE"):
        raise PipelineError(f"Set {env_var} to the Claude Managed Agents vault id.")
    if vault_id.startswith("sk-ant-"):
        raise PipelineError(
            f"{env_var} currently contains an Anthropic API key. Paste the "
            f"{provider} vault id from Claude Console instead; it should start with 'vlt_'."
        )
    if not vault_id.startswith("vlt_"):
        raise PipelineError(f"{env_var} should start with 'vlt_'. Got: {vault_id!r}")


def _validate_mcp_url(url: str, url_env: str, vault_env: str):
    parsed = urlparse(url)
    if not url or "REPLACE-ME" in url or parsed.scheme != "https" or not parsed.netloc:
        raise PipelineError(
            f"Set {url_env} to a valid https MCP endpoint, or use a "
            f"{vault_env} whose credential contains an MCP server URL."
        )


def _mcp_url_from_vault(client, vault_id: str, provider: str) -> str:
    credentials = list(client.beta.vaults.credentials.list(vault_id, betas=config.BETAS))
    mcp_credentials = [
        c for c in credentials
        if getattr(getattr(c, "auth", None), "type", None) == "mcp_oauth"
    ]
    provider_credentials = [
        c for c in mcp_credentials
        if provider.lower() in (
            f"{getattr(c, 'display_name', '') or ''} "
            f"{getattr(getattr(c, 'auth', None), 'mcp_server_url', '')}"
        ).lower()
    ]
    if len(provider_credentials) == 1:
        return provider_credentials[0].auth.mcp_server_url
    if len(mcp_credentials) == 1:
        return mcp_credentials[0].auth.mcp_server_url
    raise PipelineError(
        f"Could not uniquely identify the {provider} MCP credential in vault {vault_id}. "
        "Set the matching MCP URL env var explicitly."
    )


def _resolve_mcp_connection(client, provider: str, vault_id: str, mcp_url: str, url_env: str, vault_env: str):
    _validate_vault_id(vault_id, vault_env, provider.title())
    mcp_url = mcp_url or _mcp_url_from_vault(client, vault_id, provider)
    _validate_mcp_url(mcp_url, url_env, vault_env)
    return mcp_url


# --------------------------------------------------------------------------
# Agent roster - now data-driven. Which agents exist, their prompts, and
# their delegation order all come from a "team" bundle (see
# db.get_team_with_members) built from the agent_specs catalog + team
# snapshot at team-creation time, not from module constants here. The
# roster is still one level deep by platform design: the coordinator (the
# team member with is_coordinator=1) delegates to every other member, and
# no specialist can delegate further. Platform cap: 20 agents in the
# roster, 1 level deep.
#
# Tool configs are the one thing NOT stored in SQLite (they're nested
# dict/list shapes the SDK expects, not something the Agent Specs UI lets
# anyone edit in this pass) - kept here as a small mapping by role_key
# instead.
# --------------------------------------------------------------------------

_SCOPED_WRITE_READ = [{
    "type": "agent_toolset_20260401",
    "default_config": {"enabled": False},
    "configs": [
        {"name": "write", "enabled": True},
        {"name": "read", "enabled": True},
    ],
}]

# The Developer (Smith) needs the full, unscoped toolset (including bash)
# to install dependencies, run a local dev/build command, and zip the
# finished site - unlike the BA/Architect, who only ever write/read
# markdown.
_DEV_TOOLSET = [{"type": "agent_toolset_20260401"}]

_TOOLS_BY_ROLE = {
    "business_analyst": _SCOPED_WRITE_READ,
    "solution_architect": _SCOPED_WRITE_READ,
    "developer": _DEV_TOOLSET,
}


# --------------------------------------------------------------------------
# Agent / memory-store caching
# --------------------------------------------------------------------------


def _load_cache() -> dict:
    path = Path(config.AGENT_CACHE_PATH)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_cache(cache: dict):
    Path(config.AGENT_CACHE_PATH).write_text(json.dumps(cache, indent=2))


def _build_closing_steps(specialists: list, start_index: int) -> str:
    """Builds the coordinator's closing numbered steps, conditional on
    which roles are actually on the team - a Developer-only team shouldn't
    claim to file a "BRD" that was never produced, and a BA/Architect-only
    team shouldn't be told to ship a website that was never built."""
    role_keys = {m["role_key"] for m in specialists}
    has_docs = bool({"business_analyst", "solution_architect"} & role_keys)
    has_dev = "developer" in role_keys

    steps = []
    n = start_index
    steps.append(f"{n}. Copy every finished deliverable to /mnt/session/outputs/.")
    n += 1
    if has_docs:
        steps.append(
            f"{n}. File the BRD and/or Technical Design Document produced above to Google Docs "
            "under a \"Delivery\" folder via the google_docs MCP server - one Google Doc per "
            "document, titled after the project name plus \"BRD\" or \"Technical Design "
            "Document\"."
        )
        n += 1
    if has_dev:
        steps.append(
            f"{n}. Do not file the website itself to Google Docs and do not deploy it anywhere - "
            "/mnt/session/outputs/site.zip is the complete deliverable for the website."
        )
        n += 1
    slack_tail = " and linking any filed Google Docs" if has_docs else ""
    steps.append(
        f"{n}. Post at most two short updates to the configured Slack channel via the slack MCP "
        f"server: one after the first specialist finishes, one final message summarizing what "
        f"was delivered{slack_tail}."
    )
    n += 1
    steps.append(
        f"{n}. Append a short summary of this run (project name, key decisions, and links/paths "
        "to everything delivered) to /mnt/memory/project-context/log.md so future runs of the "
        "same project recall it."
    )
    return "\n".join(steps)


def _build_coordinator_system(coordinator: dict, specialists: list) -> str:
    """Assembles the coordinator's system prompt from its stored template
    (coordinator["system_prompt"], normally DELIVERY_COORDINATOR_BOILERPLATE
    from prompts.py, containing the literal {{DELEGATION_STEPS}} and
    {{CLOSING_STEPS}} tokens) plus a numbered delegation list built from
    each specialist's handoff_instructions, in sequence order. Uses
    str.replace rather than str.format/Jinja so a literal "{" anywhere in
    an edited prompt (e.g. a JSON example) can't raise a KeyError."""
    delegation = "\n".join(
        f"{i}. Delegate to {m['display_name']} ({m['role_title']}). {m['handoff_instructions']}"
        for i, m in enumerate(specialists, start=1)
    )
    closing = _build_closing_steps(specialists, start_index=len(specialists) + 1)
    system = (
        coordinator["system_prompt"]
        .replace("{{DELEGATION_STEPS}}", delegation)
        .replace("{{CLOSING_STEPS}}", closing)
    )
    return system + (
        f"\n\nSlack target channel: {config.SLACK_CHANNEL}. "
        "Use the slack MCP server for the status updates only."
    )


def _build_outcome_description(project_name: str, project_brief: str, team: dict) -> str:
    role_keys = {m["role_key"] for m in team["members"]}
    deliverables = []
    if {"business_analyst", "solution_architect"} & role_keys:
        deliverables.append("a BRD and a Technical Design Document, filed to Google Docs")
    if "developer" in role_keys:
        deliverables.append("a runnable website packaged as site.zip")
    deliverables_text = " and ".join(deliverables) if deliverables else "the requested deliverables"
    return (
        f"Project: {project_name}. Brief: {project_brief} "
        f"Produce {deliverables_text}, and post concise progress and completion updates to "
        f"Slack channel {config.SLACK_CHANNEL}."
    )


def _build_delivery_rubric_section(role_keys: set) -> str:
    bullets = ["- Every deliverable produced above is saved to /mnt/session/outputs/."]
    if {"business_analyst", "solution_architect"} & role_keys:
        bullets.append(
            "- Any BRD/TDD produced is filed to Google Docs under a \"Delivery\" folder via the "
            "google_docs MCP server."
        )
    bullets.append("- At most two Slack updates are posted to the configured channel.")
    return "## Delivery\n" + "\n".join(bullets) + "\n"


def _build_rubric(team: dict) -> str:
    role_keys = {m["role_key"] for m in team["members"]}
    parts = ["# Delivery Rubric\n"]
    if "business_analyst" in role_keys:
        parts.append(RUBRIC_BRD_SECTION)
    if "solution_architect" in role_keys:
        parts.append(RUBRIC_TDD_SECTION)
    if "developer" in role_keys:
        parts.append(RUBRIC_SITE_SECTION)
    parts.append(_build_delivery_rubric_section(role_keys))
    return "\n".join(parts)


def _get_or_create_agents(client, team: dict) -> dict:
    """Create the team's coordinator + every specialist, cache ids under
    each member's team-namespaced cache_key (see db.create_team - avoids
    two different teams' e.g. "business_analyst" role colliding in the
    shared agent_cache.json).

    Caches each id as soon as it's created, not just at the end - a retry
    after a partial failure resumes from whatever is already cached
    instead of creating a fresh, duplicate set of agents.
    """
    cache = _load_cache()
    coordinator = next(m for m in team["members"] if m["is_coordinator"])
    specialists = sorted(
        (m for m in team["members"] if not m["is_coordinator"]),
        key=lambda m: m["sequence"],
    )

    for member in specialists:
        if member["cache_key"] not in cache:
            tools = _TOOLS_BY_ROLE.get(member["role_key"], _SCOPED_WRITE_READ)
            agent = client.beta.agents.create(
                name=f"{team['name']} · {member['display_name']} ({member['role_title']})",
                model=config.MODEL, system=member["system_prompt"],
                tools=tools, betas=config.BETAS,
            )
            cache[member["cache_key"]] = agent.id
            _save_cache(cache)

    if coordinator["cache_key"] not in cache:
        coordinator_system = _build_coordinator_system(coordinator, specialists)
        coordinator_agent = client.beta.agents.create(
            name=f"{team['name']} · {coordinator['display_name']} ({coordinator['role_title']})",
            model=config.MODEL,
            system=coordinator_system,
            mcp_servers=[
                {"type": "url", "name": "google_docs", "url": config.GOOGLE_DOCS_MCP_URL},
                {"type": "url", "name": "slack", "url": config.SLACK_MCP_URL},
            ],
            tools=[
                {"type": "agent_toolset_20260401"},
                {
                    "type": "mcp_toolset",
                    "mcp_server_name": "google_docs",
                    "default_config": {"permission_policy": {"type": "always_allow"}},
                },
                {
                    "type": "mcp_toolset",
                    "mcp_server_name": "slack",
                    "default_config": {"permission_policy": {"type": "always_allow"}},
                },
            ],
            multiagent={
                "type": "coordinator",
                "agents": [
                    {"type": "agent", "id": cache[m["cache_key"]]} for m in specialists
                ],
            },
            betas=config.BETAS,
        )
        cache[coordinator["cache_key"]] = coordinator_agent.id
        _save_cache(cache)

    if "org_standards_id" not in cache:
        # NOTE: no `betas=` on memory_stores.* calls below - the installed
        # SDK auto-attaches its own required beta ("agent-memory-2026-07-22"
        # as of this writing) for this resource, and combining it with our
        # explicit "managed-agents-2026-04-01" beta gets rejected by the API
        # as two incompatible Agent Memory listing contracts (400
        # invalid_request_error). Agents/environments/sessions calls still
        # need betas=config.BETAS.
        org_standards = client.beta.memory_stores.create(
            name="delivery-org-standards",
            description="Org conventions for BRDs/TDDs: tone and default tech stack.",
        )
        client.beta.memory_stores.memories.create(
            org_standards.id, path="/style.md", content=ORG_STANDARDS_STYLE,
        )
        client.beta.memory_stores.memories.create(
            org_standards.id, path="/tech_defaults.md", content=ORG_STANDARDS_TECH_DEFAULTS,
        )
        cache["org_standards_id"] = org_standards.id
        _save_cache(cache)

    return cache


# Bumped when the environment's networking config changes shape - lets
# _get_or_create_environment detect an already-cached environment that
# still has the OLD (narrower) config and broaden it in place, exactly
# once, without ever needing a manual cache wipe. v2: added package-manager
# registries + enabled package managers, for the Developer (Smith) role.
_ENVIRONMENT_CONFIG_VERSION = 2

_ENVIRONMENT_NETWORKING_CONFIG = {
    "type": "cloud",
    "networking": {
        "type": "limited",
        "allowed_hosts": [
            "docs.googleapis.com", "www.googleapis.com",
            "registry.npmjs.org", "pypi.org", "files.pythonhosted.org",
            "repo.maven.apache.org", "api.nuget.org",
            "github.com", "raw.githubusercontent.com",
        ],
        "allow_mcp_servers": True,
        "allow_package_managers": True,
    },
}


def _get_or_create_environment(client) -> str:
    """Create the sandbox environment once and cache its id; every team
    shares this one environment (simpler than a second, Developer-only
    environment, and the broadened networking below is harmless for teams
    that never use it).

    Previously this was created fresh on every single pipeline run (every
    manual click, every scheduled tick) despite the "Creating/reusing
    environment" log message claiming otherwise - each run silently left
    behind an orphaned environment on the platform. Cached the same way as
    the agents and memory stores above.
    """
    cache = _load_cache()
    env_id = cache.get("environment_id")

    if env_id and cache.get("environment_config_version") != _ENVIRONMENT_CONFIG_VERSION:
        # Broaden the existing cached environment in place (confirmed
        # against the installed SDK that client.beta.environments.update()
        # exists and takes the same `config` shape as create()) - no need
        # to delete/recreate and re-provision.
        client.beta.environments.update(env_id, config=_ENVIRONMENT_NETWORKING_CONFIG, betas=config.BETAS)
        cache["environment_config_version"] = _ENVIRONMENT_CONFIG_VERSION
        _save_cache(cache)
    elif not env_id:
        env = client.beta.environments.create(
            name="delivery-env", config=_ENVIRONMENT_NETWORKING_CONFIG, betas=config.BETAS,
        )
        cache["environment_id"] = env.id
        cache["environment_config_version"] = _ENVIRONMENT_CONFIG_VERSION
        _save_cache(cache)

    return cache["environment_id"]


def _get_or_create_project_store(client, slug: str, project_name: str) -> str:
    # See the note above _get_or_create_agents' memory_stores.create call -
    # no betas= here either, for the same reason.
    store_name = f"delivery-project-{slug}"
    existing = [s for s in client.beta.memory_stores.list().data if s.name == store_name]
    if existing:
        return existing[0].id
    store = client.beta.memory_stores.create(
        name=store_name, description=f"Per-project delivery log for: {project_name}",
    )
    return store.id


def clear_agent_cache():
    """Wipe the local agent/environment/memory-store cache. Called after
    teardown_platform_resources - once those platform ids are archived or
    deleted, keeping their stale ids cached would make the next run try to
    reuse resources that no longer exist (or, for agents, are permanently
    read-only). An empty cache makes the next run recreate everything from
    scratch, same as a brand-new install.
    """
    _save_cache({})


def invalidate_cached_agent(cache_key: str):
    """Drops one agent's cached platform id so the next run recreates it
    fresh. Used when a team's membership changes: the coordinator's
    multiagent roster and dynamic system prompt (see
    _build_coordinator_system) are both assembled from the specialist
    list only at agent-creation time, so an already-provisioned
    coordinator doesn't automatically pick up a newly added/removed
    specialist - clearing just its cache_key here and letting the next
    run recreate it does. The specialist agents themselves are left
    alone: a newly added one gets created normally by
    _get_or_create_agents; a removed one's platform agent is simply no
    longer referenced (Managed Agents has no agent delete, only the
    teardown flow's archive)."""
    cache = _load_cache()
    if cache.pop(cache_key, None) is not None:
        _save_cache(cache)


# --------------------------------------------------------------------------
# Platform teardown - stop paying for what you're not using
# --------------------------------------------------------------------------


@dataclass
class TeardownResult:
    deleted: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)


def teardown_platform_resources(client, session_ids, agent_roster, *, include_vaults: bool = False) -> TeardownResult:
    """Removes every Managed Agents platform resource this app has created,
    so nothing keeps running (or keeps being reusable) after you're done
    experimenting. Order matters - each step depends on the one before it
    being done first:

    1. Every session this app ever created (session_ids, read from the
       local runs table by the caller) - an environment can't be deleted
       while any session still references it, deleted or not.
    2. The shared sandbox environment.
    3. Every memory store this app created: the org-standards store (its id
       is cached) plus every per-project store (not individually cached -
       found by listing and matching the "delivery-project-" name prefix
       every project store is created with).
    4. Every agent every team ever created (agent_roster - a list of
       {"cache_key":, "label":} dicts built by the caller from
       db.list_all_team_members(), across every team, not just one fixed
       roster). Managed Agents has **no agent delete endpoint** - only
       `archive`, which is permanent (read-only forever, no unarchive).
       This is a platform limitation, not a choice made here. Archiving
       still stops the agent from being usable for new sessions, which is
       the main thing that matters for "stop this from costing anything
       else."
    5. Only if include_vaults=True: the Google Docs and Slack vaults
       themselves. These are NOT created by this app - you connect them
       directly in Console - so this is opt-in and off by default.
       Deleting a vault revokes its stored OAuth credentials; getting it
       back means reconnecting in Console, not just re-running this app.
       Vaults also don't really drive ongoing cost by existing (they just
       hold credentials), so there's rarely a reason to include this.

    Every step is independent and best-effort: one resource already being
    gone, or one call failing, doesn't stop the rest from being attempted.
    Returns a TeardownResult so the caller can show what actually happened
    instead of a blind "done".
    """
    result = TeardownResult()
    cache = _load_cache()

    # 1. Sessions - delete-only; the API refuses to delete a still-running
    # session (the dashboard route already refuses to start teardown while
    # any local run is 'running', but a session could in principle be
    # running from outside this app too).
    for session_id in session_ids:
        try:
            client.beta.sessions.delete(session_id, betas=config.BETAS)
            result.deleted.append(f"session {session_id}")
        except Exception as exc:  # noqa: BLE001 - best-effort, report and continue
            result.failed.append(f"session {session_id}: {exc}")

    # 2. Environment - can only be deleted once nothing references it,
    # which is why sessions go first.
    environment_id = cache.get("environment_id")
    if environment_id:
        try:
            client.beta.environments.delete(environment_id, betas=config.BETAS)
            result.deleted.append(f"environment {environment_id}")
        except Exception as exc:  # noqa: BLE001
            result.failed.append(f"environment {environment_id}: {exc}")
    else:
        result.skipped.append("environment (none cached)")

    # 3. Memory stores - no betas= on memory_stores.* calls, same reason as
    # everywhere else they're called in this file (see the comment above
    # _get_or_create_agents's memory_stores.create call).
    org_standards_id = cache.get("org_standards_id")
    if org_standards_id:
        try:
            client.beta.memory_stores.delete(org_standards_id)
            result.deleted.append(f"memory store {org_standards_id} (org-standards)")
        except Exception as exc:  # noqa: BLE001
            result.failed.append(f"memory store {org_standards_id}: {exc}")
    else:
        result.skipped.append("org-standards memory store (none cached)")

    try:
        project_stores = [
            s for s in client.beta.memory_stores.list().data
            if s.name.startswith("delivery-project-")
        ]
    except Exception as exc:  # noqa: BLE001
        project_stores = []
        result.failed.append(f"listing project memory stores: {exc}")
    for store in project_stores:
        try:
            client.beta.memory_stores.delete(store.id)
            result.deleted.append(f"memory store {store.id} ({store.name})")
        except Exception as exc:  # noqa: BLE001
            result.failed.append(f"memory store {store.id} ({store.name}): {exc}")

    # 4. Agents - archive only. See the docstring above for why there's no
    # delete option here.
    for member in agent_roster:
        agent_id = cache.get(member["cache_key"])
        label = member["label"]
        if not agent_id:
            result.skipped.append(f"{label} (none cached)")
            continue
        try:
            client.beta.agents.archive(agent_id, betas=config.BETAS)
            result.deleted.append(f"agent {agent_id} ({label}, archived)")
        except Exception as exc:  # noqa: BLE001
            result.failed.append(f"agent {agent_id} ({label}): {exc}")

    # 5. Vaults - opt-in only. Not created by this app - see the docstring.
    if include_vaults:
        for env_var, vault_id in (
            ("GOOGLE_DOCS_VAULT_ID", config.GOOGLE_DOCS_VAULT_ID),
            ("SLACK_VAULT_ID", config.SLACK_VAULT_ID),
        ):
            if not vault_id:
                result.skipped.append(f"{env_var} (not configured)")
                continue
            try:
                client.beta.vaults.delete(vault_id, betas=config.BETAS)
                result.deleted.append(f"vault {vault_id} ({env_var})")
            except Exception as exc:  # noqa: BLE001
                result.failed.append(f"vault {vault_id} ({env_var}): {exc}")

    return result


# --------------------------------------------------------------------------
# The public entry point
# --------------------------------------------------------------------------


def run_delivery_pipeline(
    project_name: str,
    project_brief: str,
    team: dict,
    *,
    max_iterations: int = None,
    on_event: Optional[Callable[[dict], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> PipelineResult:
    """Run the full delivery pipeline for one project, using the given
    team bundle (see db.get_team_with_members) to determine which agents
    exist and what they're asked to deliver.

    on_event, if given, is called with a small dict for every meaningful
    stream event (thread spawned, specialist returned, mcp call, grading
    result) - the Flask run_manager uses this to append a live log a user
    could poll, without needing to parse Managed Agents event objects
    itself.

    should_stop, if given, is polled once per streamed event (see the loop
    below) - if it returns True, PipelineStopped is raised and the run ends
    early. This is a cooperative, best-effort cancel: it's only checked
    between events, so a stop can't interrupt a single slow event/tool call
    already in flight, and it does not force-terminate the remote Managed
    Agents session itself - the platform-side session simply keeps going (or
    idles out) with nothing further recorded locally. run_manager.py's
    Stop button also marks the run 'failed' in the database immediately
    regardless of whether this callback ever fires, so the dashboard never
    waits on it.
    """
    from anthropic import Anthropic  # imported lazily so tests can run without the package configured

    if not config.ANTHROPIC_API_KEY:
        raise PipelineError("ANTHROPIC_API_KEY is not configured.")

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    max_iterations = max_iterations or config.MAX_ITERATIONS

    def emit(kind: str, **payload):
        event = {"kind": kind, **payload}
        if on_event:
            on_event(event)
        return event

    slug = slugify(project_name)

    emit("status", message="Resolving Google Docs and Slack vaults")
    google_mcp_url = _resolve_mcp_connection(
        client, "google", config.GOOGLE_DOCS_VAULT_ID, config.GOOGLE_DOCS_MCP_URL,
        "GOOGLE_DOCS_MCP_URL", "GOOGLE_DOCS_VAULT_ID",
    )
    config.GOOGLE_DOCS_MCP_URL = google_mcp_url
    slack_mcp_url = _resolve_mcp_connection(
        client, "slack", config.SLACK_VAULT_ID, config.SLACK_MCP_URL,
        "SLACK_MCP_URL", "SLACK_VAULT_ID",
    )
    config.SLACK_MCP_URL = slack_mcp_url

    emit("status", message="Creating/reusing environment")
    environment_id = _get_or_create_environment(client)

    emit("status", message="Creating/reusing agents")
    agent_ids = _get_or_create_agents(client, team)

    emit("status", message="Creating/reusing project memory store")
    project_store_id = _get_or_create_project_store(client, slug, project_name)

    coordinator = next(m for m in team["members"] if m["is_coordinator"])
    session = client.beta.sessions.create(
        agent={"type": "agent", "id": agent_ids[coordinator["cache_key"]]},
        environment_id=environment_id,
        vault_ids=[config.GOOGLE_DOCS_VAULT_ID, config.SLACK_VAULT_ID],
        resources=[
            {"type": "memory_store", "memory_store_id": agent_ids["org_standards_id"], "access": "read_only"},
            {"type": "memory_store", "memory_store_id": project_store_id, "access": "read_write"},
        ],
        title=project_name,
        betas=config.BETAS,
    )
    emit("session_created", session_id=session.id)

    satisfied = False
    with client.beta.sessions.events.stream(session.id, betas=config.BETAS) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[{
                "type": "user.define_outcome",
                "description": _build_outcome_description(project_name, project_brief, team),
                "rubric": {"type": "text", "content": _build_rubric(team)},
                "max_iterations": max_iterations,
            }],
            betas=config.BETAS,
        )
        for event in stream:
            if should_stop and should_stop():
                emit("status", message="Stop requested - ending run.")
                raise PipelineStopped("Stopped by user.")
            if event.type == "session.thread_created":
                emit("thread_created", agent_name=event.agent_name)
            elif event.type == "agent.thread_message_received":
                emit("thread_returned", from_agent_name=event.from_agent_name)
            elif event.type == "agent.mcp_tool_use":
                emit("mcp_tool_use", name=event.name)
            elif event.type == "span.outcome_evaluation_end":
                satisfied = event.result == "satisfied"
                emit("outcome_evaluation", iteration=event.iteration, result=event.result)
            elif event.type == "session.status_idle":
                emit("status", message="session idle")
                break

    project_dir = config.OUTPUTS_DIR / slug
    project_dir.mkdir(exist_ok=True)
    role_keys = {m["role_key"] for m in team["members"]}
    wanted = {}
    if "business_analyst" in role_keys:
        wanted["BRD.md"] = project_dir / "BRD.md"
    if "solution_architect" in role_keys:
        wanted["Technical_Design_Document.md"] = project_dir / "Technical_Design_Document.md"
    if "developer" in role_keys:
        wanted["site.zip"] = project_dir / "site.zip"
    saved = {}
    for f in client.beta.files.list(scope_id=session.id, betas=config.BETAS):
        if f.filename in wanted:
            client.beta.files.download(f.id, betas=config.BETAS).write_to_file(str(wanted[f.filename]))
            saved[f.filename] = str(wanted[f.filename])
            emit("file_saved", filename=f.filename, path=str(wanted[f.filename]))

    session = client.beta.sessions.retrieve(session.id, betas=config.BETAS)
    cost = estimate_session_cost(session, config.MODEL)

    return PipelineResult(
        session_id=session.id,
        satisfied=satisfied,
        brd_path=saved.get("BRD.md"),
        tdd_path=saved.get("Technical_Design_Document.md"),
        site_path=saved.get("site.zip"),
        cost_usd=cost["total_cost"],
    )
