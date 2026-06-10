"""Stage one of the agent: deterministic risk rules over the warehouse.

Three rules, with boundary semantics pinned down in tests/test_signals.py:

* renewal_upcoming: renewal_date within 60 days, inclusive. Day 60 fires,
  day 61 does not, past renewals do not.
* usage_drop: trailing-14-day weighted usage down strictly more than 40%
  vs the prior 14 days. A drop of exactly 40% does not fire.
* champion_silent: the account's champion inactive strictly more than
  30 days. Exactly 30 does not fire.

Severity starts at 1 per signal, +1 when two or more signals stack on the
same account, +1 when ARR is at or above $100K, capped at 3. The LLM never
decides whether an account is at risk, only how to phrase the next action.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb

from revops import config

RENEWAL_UPCOMING = "renewal_upcoming"
USAGE_DROP = "usage_drop"
CHAMPION_SILENT = "champion_silent"

SIGNAL_LABELS = {
    RENEWAL_UPCOMING: "Renewal approaching",
    USAGE_DROP: "Usage falling",
    CHAMPION_SILENT: "Champion gone quiet",
}


@dataclass
class Signal:
    account_id: int
    account_name: str
    signal: str
    severity: int = 1
    # Data behind the rule; fed to the narrator and shown in the dashboard
    # reasoning trace. Only this account's relevant rows.
    context: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return SIGNAL_LABELS.get(self.signal, self.signal)


def detect_renewal_upcoming(con: duckdb.DuckDBPyConnection, today: date) -> list[Signal]:
    rows = con.execute(
        """
        SELECT id, name, arr, owner_rep, renewal_date,
               datediff('day', ?, renewal_date) AS days_until
        FROM accounts
        WHERE renewal_date IS NOT NULL
          AND renewal_date >= ?
          AND datediff('day', ?, renewal_date) <= ?
        ORDER BY renewal_date
        """,
        [today, today, today, config.RENEWAL_WINDOW_DAYS],
    ).fetchall()
    return [
        Signal(
            account_id=r[0],
            account_name=r[1],
            signal=RENEWAL_UPCOMING,
            context={
                "arr": r[2],
                "owner_rep": r[3],
                "renewal_date": r[4].isoformat(),
                "days_until_renewal": r[5],
            },
        )
        for r in rows
    ]


def detect_usage_drop(con: duckdb.DuckDBPyConnection, today: date) -> list[Signal]:
    window = config.USAGE_WINDOW_DAYS
    # Recent window: the `window` days ending today (inclusive).
    # Prior window: the `window` days immediately before that.
    recent_start = today - timedelta(days=window - 1)
    prior_start = today - timedelta(days=2 * window - 1)
    end_exclusive = today + timedelta(days=1)
    rows = con.execute(
        """
        WITH windows AS (
            SELECT a.id, a.name, a.arr, a.owner_rep,
                   COALESCE(SUM(u.weight) FILTER (
                       WHERE u.ts >= ? AND u.ts < ?
                   ), 0) AS recent,
                   COALESCE(SUM(u.weight) FILTER (
                       WHERE u.ts >= ? AND u.ts < ?
                   ), 0) AS prior
            FROM accounts a
            LEFT JOIN usage_events u ON u.account_id = a.id
            GROUP BY a.id, a.name, a.arr, a.owner_rep
        )
        SELECT id, name, arr, owner_rep, recent, prior,
               (prior - recent) / prior AS drop_pct
        FROM windows
        WHERE prior > 0 AND (prior - recent) / prior > ?
        ORDER BY drop_pct DESC
        """,
        [
            recent_start,
            end_exclusive,
            prior_start,
            recent_start,
            config.USAGE_DROP_THRESHOLD,
        ],
    ).fetchall()
    return [
        Signal(
            account_id=r[0],
            account_name=r[1],
            signal=USAGE_DROP,
            context={
                "arr": r[2],
                "owner_rep": r[3],
                "recent_14d_usage": round(r[4], 1),
                "prior_14d_usage": round(r[5], 1),
                "drop_pct": round(r[6] * 100, 1),
            },
        )
        for r in rows
    ]


def detect_champion_silent(con: duckdb.DuckDBPyConnection, today: date) -> list[Signal]:
    rows = con.execute(
        """
        SELECT a.id, a.name, a.arr, a.owner_rep, c.name, c.title,
               c.last_active_date,
               datediff('day', c.last_active_date, ?) AS silent_days
        FROM accounts a
        JOIN contacts c ON c.id = a.champion_contact_id
        WHERE c.last_active_date IS NOT NULL
          AND datediff('day', c.last_active_date, ?) > ?
        ORDER BY silent_days DESC
        """,
        [today, today, config.CHAMPION_SILENCE_DAYS],
    ).fetchall()
    return [
        Signal(
            account_id=r[0],
            account_name=r[1],
            signal=CHAMPION_SILENT,
            context={
                "arr": r[2],
                "owner_rep": r[3],
                "champion_name": r[4],
                "champion_title": r[5],
                "last_active_date": r[6].isoformat(),
                "silent_days": r[7],
            },
        )
        for r in rows
    ]


def apply_severity(signals: list[Signal]) -> list[Signal]:
    """Score severity: 1 base, +1 for stacked signals, +1 for high ARR, cap 3.

    Mutates and returns the same list.
    """
    per_account: dict[int, int] = {}
    for s in signals:
        per_account[s.account_id] = per_account.get(s.account_id, 0) + 1
    for s in signals:
        severity = 1
        if per_account[s.account_id] >= 2:
            severity += 1
        if s.context.get("arr", 0) >= config.HIGH_ARR_THRESHOLD:
            severity += 1
        s.severity = min(severity, 3)
    return signals


def detect_all(con: duckdb.DuckDBPyConnection, today: date | None = None) -> list[Signal]:
    """Run every rule and score severity. Read-only, no writeback here."""
    today = today or date.today()
    signals = (
        detect_renewal_upcoming(con, today)
        + detect_usage_drop(con, today)
        + detect_champion_silent(con, today)
    )
    return apply_severity(signals)
