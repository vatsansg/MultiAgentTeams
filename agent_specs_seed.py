"""Seed data for the agent_specs catalog, inserted once by
db._seed_agent_specs_if_empty() on a fresh/upgraded database.

Imported lazily (inside that function, not at db.py's module level) so
db.py itself never needs the labs/shared sys.path trick pipeline.py
already does.
"""
import json
import sys

import config

sys.path.insert(0, config.LABS_SHARED_DIR)
from prompts import (  # noqa: E402
    DELIVERY_COORDINATOR_BOILERPLATE,
    BA_SPECIALIST_SYSTEM, BA_HANDOFF_INSTRUCTIONS,
    ARCHITECT_SPECIALIST_SYSTEM, ARCHITECT_HANDOFF_INSTRUCTIONS,
    DEVELOPER_SPECIALIST_SYSTEM, DEVELOPER_HANDOFF_INSTRUCTIONS,
)

DEFAULT_AGENT_SPECS = [
    {
        "role_key": "delivery_lead", "display_name": "Delivery Lead", "role_title": "Coordinator",
        "description": ("Reads org standards, delegates to each specialist below in turn, grades "
                         "the outcome, files documents to Google Docs, posts Slack updates."),
        "system_prompt": DELIVERY_COORDINATOR_BOILERPLATE, "handoff_instructions": None,
        "skills": json.dumps(["Delegation", "Coordination", "Google Docs filing", "Slack updates"]),
        "tools_label": "Built-in toolset + google_docs MCP + slack MCP",
        "is_coordinator": 1, "sequence": 0,
    },
    {
        "role_key": "business_analyst", "display_name": "Roger", "role_title": "Business Analyst",
        "description": ("Turns a project brief into a Business Requirements Document. Makes "
                         "clearly labeled assumptions instead of asking questions - this pipeline "
                         "runs unattended."),
        "system_prompt": BA_SPECIALIST_SYSTEM, "handoff_instructions": BA_HANDOFF_INSTRUCTIONS,
        "skills": json.dumps(["Requirements analysis", "Stakeholder framing", "BRD authoring"]),
        "tools_label": "write, read (scoped)", "is_coordinator": 0, "sequence": 1,
    },
    {
        "role_key": "solution_architect", "display_name": "Michael", "role_title": "Solution Architect",
        "description": ("Turns the BRD into a Technical Design Document, mapping every "
                         "requirement to a concrete technical decision."),
        "system_prompt": ARCHITECT_SPECIALIST_SYSTEM, "handoff_instructions": ARCHITECT_HANDOFF_INSTRUCTIONS,
        "skills": json.dumps(["System architecture", "Technical design", "Requirements traceability"]),
        "tools_label": "write, read (scoped)", "is_coordinator": 0, "sequence": 2,
    },
    {
        "role_key": "developer", "display_name": "Smith", "role_title": "Developer",
        "description": ("Builds a small, runnable website that best fits the Technical Design "
                         "Document (or the brief, if no TDD exists), packaged to run locally."),
        "system_prompt": DEVELOPER_SPECIALIST_SYSTEM, "handoff_instructions": DEVELOPER_HANDOFF_INSTRUCTIONS,
        "skills": json.dumps([
            "Node.js", "Angular", "React", "Python", "Java", "ASP.NET Core",
            "SQLite", "MySQL", "CosmosDB", "SQL Server", "Oracle", "Responsive design",
        ]),
        "tools_label": "full toolset incl. bash (unscoped)", "is_coordinator": 0, "sequence": 3,
    },
]
