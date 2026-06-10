"""Configuration, read from environment variables.

Missing keys are not an error: the app falls back to offline mode (template
narrator, logged Slack alerts).
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- storage ---------------------------------------------------------------
DATA_DIR = Path(os.environ.get("REVOPS_DATA_DIR", PROJECT_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.environ.get("REVOPS_DB_PATH", str(DATA_DIR / "revops.duckdb")))

# --- signal rule thresholds --------------------------------------------------
RENEWAL_WINDOW_DAYS = 60  # renewal_date within N days fires (inclusive)
USAGE_DROP_THRESHOLD = 0.40  # trailing-14d usage down strictly more than 40%
USAGE_WINDOW_DAYS = 14
CHAMPION_SILENCE_DAYS = 30  # champion inactive strictly more than N days fires
HIGH_ARR_THRESHOLD = 100_000  # ARR at or above this bumps severity

# --- seed -------------------------------------------------------------------
RANDOM_SEED = 42
USAGE_HISTORY_DAYS = 28

# --- dashboard ----------------------------------------------------------------
DASHBOARD_URL = os.environ.get("REVOPS_DASHBOARD_URL", "http://localhost:8000")


def anthropic_api_key() -> str | None:
    """Claude API key, or None to run in offline (template narrator) mode."""
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY") or None


def anthropic_model() -> str:
    return os.environ.get("REVOPS_MODEL", "claude-opus-4-8")


def slack_webhook_url() -> str | None:
    """Slack incoming webhook, or None to log alerts instead of sending."""
    return os.environ.get("SLACK_WEBHOOK_URL") or None
