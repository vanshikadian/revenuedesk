"""Messy CRM rows must be cleaned rather than crash, and the demo data must load."""

from datetime import date

import pytest

from revops import db, loaders, seed

TODAY = date(2026, 6, 9)


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    raw_dir = seed.write_csvs(raw_dir=tmp_path_factory.mktemp("raw"), today=TODAY)
    con = db.connect(":memory:")
    report = loaders.load_all(con, raw_dir)
    yield con, report
    con.close()


def test_all_clean_accounts_load(loaded):
    con, report = loaded
    assert report.accounts_loaded == 14  # 12 story accounts + 2 cleaned messy rows
    assert con.execute("SELECT count(*) FROM accounts").fetchone()[0] == 14


def test_duplicate_account_id_is_dropped(loaded):
    con, report = loaded
    assert report.accounts_dropped == 1
    rows = con.execute("SELECT name FROM accounts WHERE id = 1").fetchall()
    assert rows == [("Northwind Logistics",)]  # first occurrence wins


def test_malformed_renewal_date_loads_as_null(loaded):
    con, _ = loaded
    renewal = con.execute(
        "SELECT renewal_date FROM accounts WHERE name = 'Zenith Retail'"
    ).fetchone()[0]
    assert renewal is None


def test_unknown_tier_is_coerced(loaded):
    con, _ = loaded
    tier = con.execute("SELECT tier FROM accounts WHERE name = 'Orchid Health Labs'").fetchone()[0]
    assert tier == "unknown"


def test_every_messy_row_produced_a_warning(loaded):
    _, report = loaded
    assert len(report.warnings) == 3
    text = "\n".join(report.warnings)
    assert "malformed Renewal Dt" in text
    assert "unknown Tier" in text
    assert "duplicate Account Id" in text


def test_money_and_date_parsing(loaded):
    con, _ = loaded
    arr = con.execute("SELECT arr FROM accounts WHERE name = 'Northwind Logistics'").fetchone()[0]
    assert arr == 250_000.0


def test_unexpected_headers_fail_loudly(tmp_path):
    bad = tmp_path / "accounts.csv"
    bad.write_text("id,name\n1,X\n")
    con = db.connect(":memory:")
    with pytest.raises(ValueError, match="unexpected headers"):
        loaders.load_accounts(con, bad, loaders.IngestReport())
    con.close()


def test_seed_is_deterministic(tmp_path):
    dir_a = seed.write_csvs(raw_dir=tmp_path / "a", today=TODAY)
    dir_b = seed.write_csvs(raw_dir=tmp_path / "b", today=TODAY)
    for name in ("accounts.csv", "contacts.csv", "usage_events.csv"):
        assert (dir_a / name).read_text() == (dir_b / name).read_text()
