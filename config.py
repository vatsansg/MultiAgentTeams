"""Central configuration, read from environment variables (see .env.example).

Loads a `.env` file from this folder automatically via python-dotenv, so
`cp .env.example .env` + editing it is enough - no manual `source .env` or
export needed. Real environment variables (e.g. set by a process manager)
still take precedence over `.env` file values.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = os.environ.get("AGENT_CONSOLE_DB", str(DATA_DIR / "agent_console.sqlite3"))
AGENT_CACHE_PATH = os.environ.get("AGENT_CACHE_PATH", str(DATA_DIR / "agent_cache.json"))

# Single-user login, per the "simple single-user login" choice.
CONSOLE_USERNAME = os.environ.get("CONSOLE_USERNAME", "admin")
CONSOLE_PASSWORD = os.environ.get("CONSOLE_PASSWORD", "changeme")

SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# Managed Agents / Anthropic settings, mirroring labs/.env.example.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_DOCS_VAULT_ID = os.environ.get("GOOGLE_DOCS_VAULT_ID", "")
GOOGLE_DOCS_MCP_URL = os.environ.get("GOOGLE_DOCS_MCP_URL", "")
SLACK_VAULT_ID = os.environ.get("SLACK_VAULT_ID", "")
SLACK_MCP_URL = os.environ.get("SLACK_MCP_URL", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#delivery")
MODEL = os.environ.get("MODEL", "claude-sonnet-5")
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "2"))

BETAS = ["managed-agents-2026-04-01"]

# Where downloaded BRD/TDD files get saved, one subfolder per run.
OUTPUTS_DIR = Path(os.environ.get("AGENT_CONSOLE_OUTPUTS", str(BASE_DIR / "outputs")))
OUTPUTS_DIR.mkdir(exist_ok=True)

# Path to the shared labs/ prompts module, reused rather than duplicated.
LABS_SHARED_DIR = os.environ.get(
    "LABS_SHARED_DIR", str((BASE_DIR / ".." / "labs" / "shared").resolve())
)
