from datetime import date, datetime, timedelta

import pytest

from revops import db

TODAY = date(2026, 6, 9)


@pytest.fixture()
def con():
    """Fresh in-memory warehouse with the full schema."""
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def add_account(
    con,
    account_id: int,
    name: str = "Test Co",
    arr: float = 50_000,
    tier: str = "growth",
    owner: str = "Rep",
    renewal_date: date | None = None,
    champion_contact_id: int | None = None,
    stage: str = "live",
):
    con.execute(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [account_id, name, arr, tier, owner, renewal_date, champion_contact_id, stage],
    )


def add_contact(
    con,
    contact_id: int,
    account_id: int,
    name: str = "Champ",
    title: str = "VP",
    is_champion: bool = True,
    last_active_date: date | None = None,
):
    con.execute(
        "INSERT INTO contacts VALUES (?, ?, ?, ?, ?, ?)",
        [contact_id, account_id, name, title, is_champion, last_active_date],
    )


_event_id = iter(range(1, 1_000_000))


def add_usage(con, account_id: int, day: date, weight: float):
    """One weighted usage event at noon on the given day."""
    con.execute(
        "INSERT INTO usage_events VALUES (?, ?, ?, ?, ?)",
        [
            next(_event_id),
            account_id,
            "api_call",
            datetime.combine(day, datetime.min.time().replace(hour=12)),
            weight,
        ],
    )


def fill_usage(con, account_id: int, recent_total: float, prior_total: float):
    """Distribute totals across the recent and prior 14-day windows."""
    for i in range(14):
        add_usage(con, account_id, TODAY - timedelta(days=i), recent_total / 14)
        add_usage(con, account_id, TODAY - timedelta(days=14 + i), prior_total / 14)
