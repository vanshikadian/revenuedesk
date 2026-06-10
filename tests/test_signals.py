"""Boundary-exact tests for each deterministic rule and the severity model."""

from datetime import timedelta

from revops import signals
from tests.conftest import TODAY, add_account, add_contact, fill_usage


def fired(result, signal_name):
    return [s for s in result if s.signal == signal_name]


# --- renewal_upcoming -------------------------------------------------------


def test_renewal_at_exactly_60_days_fires(con):
    add_account(con, 1, renewal_date=TODAY + timedelta(days=60))
    assert len(signals.detect_renewal_upcoming(con, TODAY)) == 1


def test_renewal_at_61_days_does_not_fire(con):
    add_account(con, 1, renewal_date=TODAY + timedelta(days=61))
    assert signals.detect_renewal_upcoming(con, TODAY) == []


def test_renewal_today_fires(con):
    add_account(con, 1, renewal_date=TODAY)
    assert len(signals.detect_renewal_upcoming(con, TODAY)) == 1


def test_past_renewal_does_not_fire(con):
    add_account(con, 1, renewal_date=TODAY - timedelta(days=1))
    assert signals.detect_renewal_upcoming(con, TODAY) == []


def test_null_renewal_date_is_skipped(con):
    add_account(con, 1, renewal_date=None)
    assert signals.detect_renewal_upcoming(con, TODAY) == []


# --- usage_drop -------------------------------------------------------------


def test_usage_drop_of_exactly_40_percent_does_not_fire(con):
    add_account(con, 1)
    fill_usage(con, 1, recent_total=60.0, prior_total=100.0)  # exactly 40% down
    assert signals.detect_usage_drop(con, TODAY) == []


def test_usage_drop_just_over_40_percent_fires(con):
    add_account(con, 1)
    fill_usage(con, 1, recent_total=59.0, prior_total=100.0)  # 41% down
    result = signals.detect_usage_drop(con, TODAY)
    assert len(result) == 1
    assert result[0].context["drop_pct"] == 41.0


def test_usage_increase_does_not_fire(con):
    add_account(con, 1)
    fill_usage(con, 1, recent_total=120.0, prior_total=100.0)
    assert signals.detect_usage_drop(con, TODAY) == []


def test_no_prior_usage_does_not_fire(con):
    """A brand-new account with zero prior usage must not divide by zero."""
    add_account(con, 1)
    fill_usage(con, 1, recent_total=10.0, prior_total=0.0)
    assert signals.detect_usage_drop(con, TODAY) == []


# --- champion_silent ----------------------------------------------------------


def test_champion_silent_exactly_30_days_does_not_fire(con):
    add_account(con, 1, champion_contact_id=10)
    add_contact(con, 10, 1, last_active_date=TODAY - timedelta(days=30))
    assert signals.detect_champion_silent(con, TODAY) == []


def test_champion_silent_31_days_fires(con):
    add_account(con, 1, champion_contact_id=10)
    add_contact(con, 10, 1, last_active_date=TODAY - timedelta(days=31))
    result = signals.detect_champion_silent(con, TODAY)
    assert len(result) == 1
    assert result[0].context["silent_days"] == 31


def test_non_champion_contact_silence_is_ignored(con):
    add_account(con, 1, champion_contact_id=10)
    add_contact(con, 10, 1, last_active_date=TODAY - timedelta(days=2))
    add_contact(con, 11, 1, is_champion=False, last_active_date=TODAY - timedelta(days=90))
    assert signals.detect_champion_silent(con, TODAY) == []


# --- severity ----------------------------------------------------------------


def test_single_signal_low_arr_is_severity_1(con):
    add_account(con, 1, arr=50_000, renewal_date=TODAY + timedelta(days=30))
    result = signals.detect_all(con, TODAY)
    assert [s.severity for s in result] == [1]


def test_high_arr_bumps_severity_to_2(con):
    add_account(con, 1, arr=100_000, renewal_date=TODAY + timedelta(days=30))
    result = signals.detect_all(con, TODAY)
    assert [s.severity for s in result] == [2]


def test_stacked_signals_bump_severity(con):
    add_account(con, 1, arr=50_000, champion_contact_id=10)
    add_contact(con, 10, 1, last_active_date=TODAY - timedelta(days=45))
    fill_usage(con, 1, recent_total=30.0, prior_total=100.0)
    result = signals.detect_all(con, TODAY)
    assert len(result) == 2
    assert all(s.severity == 2 for s in result)


def test_stacked_signals_plus_high_arr_cap_at_3(con):
    """The Northwind shape: two signals + enterprise ARR -> severity 3."""
    add_account(con, 1, arr=250_000, champion_contact_id=10)
    add_contact(con, 10, 1, last_active_date=TODAY - timedelta(days=45))
    fill_usage(con, 1, recent_total=30.0, prior_total=100.0)
    result = signals.detect_all(con, TODAY)
    assert len(result) == 2
    assert all(s.severity == 3 for s in result)


def test_three_signals_high_arr_still_capped_at_3(con):
    add_account(
        con,
        1,
        arr=250_000,
        champion_contact_id=10,
        renewal_date=TODAY + timedelta(days=10),
    )
    add_contact(con, 10, 1, last_active_date=TODAY - timedelta(days=45))
    fill_usage(con, 1, recent_total=30.0, prior_total=100.0)
    result = signals.detect_all(con, TODAY)
    assert len(result) == 3
    assert all(s.severity == 3 for s in result)
