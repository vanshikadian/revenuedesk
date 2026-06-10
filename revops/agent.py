"""The account-watching agent loop.

run_once() is one full pass:

1. Detect: deterministic rules over the warehouse (signals.py).
2. Narrate: one LLM call per fired signal (narrator.py), template offline.
3. Write back: replace account_signals with this run's conclusions.
   Idempotent, so running twice never duplicates rows.
4. Alert: Block Kit Slack message per severity-3 account (logged offline).

Also exposed as a CLI: python -m revops.agent
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import duckdb

from revops import config, db, slack
from revops import signals as signals_mod
from revops.narrator import Narrator, get_narrator
from revops.signals import Signal

log = logging.getLogger(__name__)


@dataclass
class NarratedSignal:
    signal: Signal
    recommended_action: str
    reason: str


@dataclass
class RunResult:
    signals: list[NarratedSignal] = field(default_factory=list)
    summary: str = ""
    accounts_flagged: int = 0
    arr_at_risk: float = 0.0
    alerts_sent: list[str] = field(default_factory=list)  # account names POSTed to Slack
    alerts_logged: list[str] = field(default_factory=list)  # account names logged offline
    narrator_name: str = ""


def run_once(
    con: duckdb.DuckDBPyConnection | None = None,
    today: date | None = None,
    narrator: Narrator | None = None,
) -> RunResult:
    """One full agent pass. Safe to call repeatedly; writeback is idempotent."""
    owns_connection = con is None
    con = con or db.connect()
    narrator = narrator or get_narrator()
    today = today or date.today()
    try:
        fired = signals_mod.detect_all(con, today)
        result = RunResult(narrator_name=narrator.name)

        for signal in fired:
            narration = narrator.narrate(signal)
            result.signals.append(
                NarratedSignal(
                    signal=signal,
                    recommended_action=narration.recommended_action,
                    reason=narration.reason,
                )
            )

        _write_back(con, result.signals)

        flagged = {s.signal.account_id: s.signal for s in result.signals}
        result.accounts_flagged = len(flagged)
        result.arr_at_risk = sum(s.context.get("arr", 0) for s in flagged.values())
        most_urgent = ""
        if result.signals:
            top = max(
                result.signals, key=lambda n: (n.signal.severity, n.signal.context.get("arr", 0))
            )
            most_urgent = top.signal.account_name
        result.summary = narrator.summarize(
            result.accounts_flagged, result.arr_at_risk, most_urgent
        )
        _record_run(con, result)
        _send_alerts(result)
        return result
    finally:
        if owns_connection:
            con.close()


def _write_back(con: duckdb.DuckDBPyConnection, narrated: list[NarratedSignal]) -> None:
    """Replace the agent's conclusions. Delete-then-insert keeps reruns idempotent."""
    now = datetime.now()
    con.execute("DELETE FROM account_signals")
    if narrated:
        con.executemany(
            "INSERT INTO account_signals VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    n.signal.account_id,
                    n.signal.signal,
                    n.signal.severity,
                    n.recommended_action,
                    n.reason,
                    now,
                )
                for n in narrated
            ],
        )


def _record_run(con: duckdb.DuckDBPyConnection, result: RunResult) -> None:
    next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM agent_runs").fetchone()[0]
    con.execute(
        "INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?)",
        [next_id, datetime.now(), result.summary, result.accounts_flagged, result.arr_at_risk],
    )


def _send_alerts(result: RunResult) -> None:
    """One Block Kit alert per severity-3 account."""
    by_account: dict[int, list[NarratedSignal]] = {}
    for n in result.signals:
        if n.signal.severity == 3:
            by_account.setdefault(n.signal.account_id, []).append(n)

    for account_signals in by_account.values():
        lead = account_signals[0]
        payload = slack.build_alert_blocks(
            account_name=lead.signal.account_name,
            arr=lead.signal.context.get("arr", 0),
            owner_rep=lead.signal.context.get("owner_rep", "unknown"),
            signals=[n.signal.label for n in account_signals],
            recommended_action=lead.recommended_action,
            dashboard_url=config.DASHBOARD_URL,
        )
        delivered = slack.send_alert(payload)
        if delivered:
            result.alerts_sent.append(lead.signal.account_name)
        else:
            result.alerts_logged.append(lead.signal.account_name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = run_once()
    print(f"\nAgent run complete (narrator: {result.narrator_name})")
    print(f"  {result.summary}\n")
    for n in sorted(result.signals, key=lambda x: -x.signal.severity):
        print(f"  [sev {n.signal.severity}] {n.signal.account_name}: {n.signal.label}")
        print(f"      action: {n.recommended_action}")
        print(f"      reason: {n.reason}")
    if result.alerts_sent:
        print(f"\n  Slack alerts sent: {', '.join(result.alerts_sent)}")
    if result.alerts_logged:
        print(f"\n  Slack alerts logged (no webhook configured): {', '.join(result.alerts_logged)}")


if __name__ == "__main__":
    main()
