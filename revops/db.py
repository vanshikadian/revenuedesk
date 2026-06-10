"""DuckDB warehouse access and schema.

Four core tables plus `agent_runs`. `account_signals` is where the agent
writes its conclusions; `agent_runs` keeps a small audit trail so the
dashboard can show the latest summary line.
"""

from pathlib import Path

import duckdb

from revops import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    arr DOUBLE NOT NULL,
    tier TEXT NOT NULL,
    owner_rep TEXT NOT NULL,
    renewal_date DATE,
    champion_contact_id INTEGER,
    stage TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    title TEXT NOT NULL,
    is_champion BOOLEAN NOT NULL,
    last_active_date DATE
);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    ts TIMESTAMP NOT NULL,
    weight DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS account_signals (
    account_id INTEGER NOT NULL,
    signal TEXT NOT NULL,
    severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 3),
    recommended_action TEXT NOT NULL,
    reason TEXT NOT NULL,
    computed_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY,
    run_at TIMESTAMP NOT NULL,
    summary TEXT NOT NULL,
    accounts_flagged INTEGER NOT NULL,
    arr_at_risk DOUBLE NOT NULL
);
"""


def connect(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open (and create if needed) the warehouse, ensuring the schema exists.

    Pass ":memory:" for an ephemeral database (used heavily in tests).
    """
    path = db_path if db_path is not None else config.DB_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    init_schema(con)
    return con


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)


def reset(con: duckdb.DuckDBPyConnection) -> None:
    """Delete all rows (keeps tables). Used when re-seeding."""
    for table in ("accounts", "contacts", "usage_events", "account_signals", "agent_runs"):
        con.execute(f"DELETE FROM {table}")
