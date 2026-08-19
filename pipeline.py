"""Wraps the ClaudeMultiAgent_ManagedAgent capstone-style Managed Agents
pipeline as a reusable Python function the Flask app can call in a
background thread.

This is the same logic as labs/ClaudeMultiAgent_ManagedAgent/ClaudeMultiAgent_ManagedAgent.ipynb -
coordinator (Delivery Lead) delegates to two specialists (Roger, Michael),
two memory stores ride along, an outcome rubric grades the result, the
coordinator files both documents to Google Docs and posts Slack updates -
restructured so it can run headless, triggered by a button click or a
scheduler instead of a human stepping through notebook cells.

The sandbox environment, agents, and the org-standards memory store are
all created once and cached to disk (config.AGENT_CACHE_PATH), rather than
recreated on every run.
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
    DELIVERY_COORDINATOR_SYSTEM,
    BA_SPECIALIST_SYSTEM,
    ARCHITECT_SPECIALIST_SYSTEM,
    ORG_STANDARDS_STYLE,
    ORG_STANDARDS_TECH_DEFAULTS,
    DELIVERY_RUBRIC,
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
# Agent roster - single source of truth for both agent creation (below) and
# the dashboard's org-chart view (dashboard.py imports COORDINATOR and
# SPECIALISTS from this module). The roster is one level deep by platform
# design: the coordinator delegates to every entry in SPECIALISTS, and no
# specialist can delegate further.
#
# To add a new specialist role (a Developer, a Tester, ...):
#   1. Add its system prompt as a new constant in labs/shared/prompts.py
#      and import it above.
#   2. Append one entry to SPECIALISTS below, following the same shape as
#      "roger" or "michael". Give it a distinct "cache_key" - that's the
#      field used to store its agent id in the agent cache.
#   3. Update DELIVERY_COORDINATOR_SYSTEM's numbered steps in prompts.py so
#      the coordinator's prose instructions actually say when to delegate to
#      the new specialist and what to do with its output - that part is a
#      workflow description in natural language and can't be inferred
#      automatically.
#
# Agent creation, caching, the coordinator's multiagent roster, and the
# dashboard's org-chart view all pick up a new SPECIALISTS entry
# automatically - no other code changes needed. Platform cap: 20 agents in
# the roster, 1 level deep.
# --------------------------------------------------------------------------

_SCOPED_WRITE_READ = [{
    "type": "agent_toolset_20260401",
    "default_config": {"enabled": False},
    "configs": [
        {"name": "write", "enabled": True},
        {"name": "read", "enabled": True},
    ],
}]

SPECIALISTS = [
    {
        "key": "roger",
        "cache_key": "roger_id",
        "agent_name": "Delivery BA (Roger)",
        "display_name": "Roger",
        "role": "Business Analyst",
        "description": (
            "Turns a project brief into a Business Requirements Document. "
            "Makes clearly labeled assumptions instead of asking questions - "
            "this pipeline runs unattended."
        ),
        "system": BA_SPECIALIST_SYSTEM,
        "tools_label": "write, read (scoped)",
        "tools": _SCOPED_WRITE_READ,
    },
    {
        "key": "michael",
        "cache_key": "michael_id",
        "agent_name": "Delivery Architect (Michael)",
        "display_name": "Michael",
        "role": "Solution Architect",
        "description": (
            "Turns the BRD into a Technical Design Document, mapping every "
            "requirement to a concrete technical decision."
        ),
        "system": ARCHITECT_SPECIALIST_SYSTEM,
        "tools_label": "write, read (scoped)",
        "tools": _SCOPED_WRITE_READ,
    },
]

COORDINATOR = {
    "key": "coordinator",
    "cache_key": "coordinator_id",
    "agent_name": "Delivery Lead",
    "display_name": "Delivery Lead",
    "role": "Coordinator",
    "description": (
        "Reads org standards, delegates to each specialist below in turn, "
        "grades the outcome, files both documents to Google Docs, posts "
        "Slack updates."
    ),
    "tools_label": "Built-in toolset + google_docs MCP + slack MCP",
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


def _get_or_create_agents(client):
    """Create the coordinator + every specialist in SPECIALISTS, cache ids.

    Caches each id as soon as it's created, not just at the end. If this
    function fails partway through (as it did the first time, on the
    memory-store beta conflict, after Roger/Michael/the coordinator had
    already been created on the platform), a retry resumes from whatever
    is already cached instead of creating a fresh, duplicate set of agents
    every time it's retried after a partial failure. Looping over
    SPECIALISTS also means a newly added role picks up the same
    create-once-and-cache behavior with no extra code.
    """
    cache = _load_cache()

    for spec in SPECIALISTS:
        if spec["cache_key"] not in cache:
            agent = client.beta.agents.create(
                name=spec["agent_name"], model=config.MODEL, system=spec["system"],
                tools=spec.get("tools") or _SCOPED_WRITE_READ, betas=config.BETAS,
            )
            cache[spec["cache_key"]] = agent.id
            _save_cache(cache)

    if COORDINATOR["cache_key"] not in cache:
        coordinator_system = (
            DELIVERY_COORDINATOR_SYSTEM
            + f"\n\nSlack target channel: {config.SLACK_CHANNEL}. "
            "Use the slack MCP server for the two short status updates only."
        )
        coordinator = client.beta.agents.create(
            name=COORDINATOR["agent_name"],
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
                    {"type": "agent", "id": cache[spec["cache_key"]]} for spec in SPECIALISTS
                ],
            },
            betas=config.BETAS,
        )
        cache[COORDINATOR["cache_key"]] = coordinator.id
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


def _get_or_create_environment(client) -> str:
    """Create the sandbox environment once and cache its id.

    Previously this was created fresh on every single pipeline run (every
    manual click, every scheduled tick) despite the "Creating/reusing
    environment" log message claiming otherwise - each run silently left
    behind an orphaned environment on the platform. Cached the same way as
    the agents and memory stores above.
    """
    cache = _load_cache()
    if "environment_id" not in cache:
        env = client.beta.environments.create(
            name="delivery-env",
            config={
                "type": "cloud",
                "networking": {
                    "type": "limited",
                    "allowed_hosts": ["docs.googleapis.com", "www.googleapis.com"],
                    "allow_mcp_servers": True,
                    "allow_package_managers": False,
                },
            },
            betas=config.BETAS,
        )
        cache["environment_id"] = env.id
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


# --------------------------------------------------------------------------
# Platform teardown - stop paying for what you're not using
# --------------------------------------------------------------------------


@dataclass
class TeardownResult:
    deleted: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)


def teardown_platform_resources(client, session_ids, *, include_vaults: bool = False) -> TeardownResult:
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
    4. Every agent this app created. Managed Agents has **no agent delete
       endpoint** - only `archive`, which is permanent (read-only forever,
       no unarchive). This is a platform limitation, not a choice made
       here. Archiving still stops the agent from being usable for new
       sessions, which is the main thing that matters for "stop this from
       costing anything else."
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
    for spec in [COORDINATOR, *SPECIALISTS]:
        agent_id = cache.get(spec["cache_key"])
        if not agent_id:
            result.skipped.append(f"{spec['display_name']} (none cached)")
            continue
        try:
            client.beta.agents.archive(agent_id, betas=config.BETAS)
            result.deleted.append(f"agent {agent_id} ({spec['display_name']}, archived)")
        except Exception as exc:  # noqa: BLE001
            result.failed.append(f"agent {agent_id} ({spec['display_name']}): {exc}")

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
    *,
    max_iterations: int = None,
    on_event: Optional[Callable[[dict], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> PipelineResult:
    """Run the full BA -> Architect delivery pipeline for one project.

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
    agent_ids = _get_or_create_agents(client)

    emit("status", message="Creating/reusing project memory store")
    project_store_id = _get_or_create_project_store(client, slug, project_name)

    session = client.beta.sessions.create(
        agent={"type": "agent", "id": agent_ids["coordinator_id"]},
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
                "description": (
                    f"Project: {project_name}. Brief: {project_brief} "
                    "Produce a BRD and a Technical Design Document, file both to "
                    f"Google Docs, and post concise progress and completion updates "
                    f"to Slack channel {config.SLACK_CHANNEL}."
                ),
                "rubric": {"type": "text", "content": DELIVERY_RUBRIC},
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
    wanted = {
        "BRD.md": project_dir / "BRD.md",
        "Technical_Design_Document.md": project_dir / "Technical_Design_Document.md",
    }
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
        cost_usd=cost["total_cost"],
    )
