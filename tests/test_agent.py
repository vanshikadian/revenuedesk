"""Agent loop: writeback, idempotency, severity-3 alerts, offline narrator.

No network or API keys needed; the TemplateNarrator is injected explicitly.
"""

from datetime import date

import pytest

from revops import agent, db, loaders, seed
from revops.narrator import TemplateNarrator

TODAY = date(2026, 6, 9)


@pytest.fixture()
def warehouse(tmp_path):
    raw_dir = seed.write_csvs(raw_dir=tmp_path / "raw", today=TODAY)
    con = db.connect(":memory:")
    loaders.load_all(con, raw_dir)
    yield con
    con.close()


def run(con):
    return agent.run_once(con=con, today=TODAY, narrator=TemplateNarrator())


def test_signals_are_written_back(warehouse):
    result = run(warehouse)
    rows = warehouse.execute(
        "SELECT account_id, signal, severity, recommended_action, reason " "FROM account_signals"
    ).fetchall()
    assert len(rows) == len(result.signals) > 0
    for _, _, severity, action, reason in rows:
        assert 1 <= severity <= 3
        assert action and reason


def test_second_run_does_not_duplicate_signals(warehouse):
    first = run(warehouse)
    count_after_first = warehouse.execute("SELECT count(*) FROM account_signals").fetchone()[0]
    second = run(warehouse)
    count_after_second = warehouse.execute("SELECT count(*) FROM account_signals").fetchone()[0]
    assert count_after_first == count_after_second == len(second.signals)
    assert len(first.signals) == len(second.signals)


def test_northwind_is_severity_3_with_stacked_signals(warehouse):
    run(warehouse)
    rows = warehouse.execute(
        """
        SELECT s.signal, s.severity FROM account_signals s
        JOIN accounts a ON a.id = s.account_id
        WHERE a.name = 'Northwind Logistics' ORDER BY s.signal
        """
    ).fetchall()
    assert rows == [("champion_silent", 3), ("usage_drop", 3)]


def test_single_signal_accounts(warehouse):
    run(warehouse)
    rows = dict(
        warehouse.execute(
            """
            SELECT a.name, s.signal FROM account_signals s
            JOIN accounts a ON a.id = s.account_id
            WHERE a.name IN ('Acme Manufacturing', 'Bluebird Media', 'Helios Energy')
            """
        ).fetchall()
    )
    assert rows == {
        "Acme Manufacturing": "renewal_upcoming",
        "Bluebird Media": "champion_silent",
        "Helios Energy": "usage_drop",
    }


def test_severity_3_alert_logged_when_no_webhook(warehouse, monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    result = run(warehouse)
    assert result.alerts_sent == []
    assert result.alerts_logged == ["Northwind Logistics"]


def test_arr_at_risk_counts_each_account_once(warehouse):
    result = run(warehouse)
    # Northwind ($250K, 2 signals) + Helios ($120K) + Acme ($85K) + Bluebird ($64K)
    assert result.arr_at_risk == 519_000.0


def test_summary_line(warehouse):
    result = run(warehouse)
    assert result.summary == (
        "4 accounts need attention, $519K ARR at risk. Most urgent: Northwind Logistics."
    )


def test_agent_run_is_recorded(warehouse):
    run(warehouse)
    run(warehouse)
    runs = warehouse.execute("SELECT count(*) FROM agent_runs").fetchone()[0]
    assert runs == 2
