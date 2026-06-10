"""CSV ingestion with explicit vendor-header mapping and cleaning.

Cleaning rules:

* Vendor headers ("Account Name", "Annual Contract Value", "Renewal Dt", ...)
  are mapped explicitly. An unknown layout raises; junk values don't.
* Malformed dates load as NULL, with a warning.
* Unknown tier values are coerced to "unknown", with a warning.
* Duplicate account ids keep the first occurrence and drop the rest.
"""

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import duckdb

ACCOUNT_HEADERS = [
    "Account Id",
    "Account Name",
    "Annual Contract Value",
    "Tier",
    "Account Owner",
    "Renewal Dt",
    "Champion Contact Id",
    "Stage",
]
CONTACT_HEADERS = [
    "Contact Id",
    "Account Id",
    "Full Name",
    "Job Title",
    "Champion Flag",
    "Last Activity",
]
EVENT_HEADERS = ["Event Id", "Account Id", "Event", "Timestamp", "Weight"]

KNOWN_TIERS = {"starter", "growth", "enterprise"}


@dataclass
class IngestReport:
    accounts_loaded: int = 0
    accounts_dropped: int = 0
    contacts_loaded: int = 0
    events_loaded: int = 0
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def pretty(self) -> str:
        lines = [
            f"  accounts: {self.accounts_loaded} loaded, {self.accounts_dropped} dropped",
            f"  contacts: {self.contacts_loaded} loaded",
            f"  usage events: {self.events_loaded} loaded",
        ]
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


def parse_money(raw: str) -> float:
    """'$250,000.00' -> 250000.0"""
    return float(raw.replace("$", "").replace(",", "").strip())


def parse_vendor_date(raw: str) -> date | None:
    """Parse MM/DD/YYYY vendor dates; returns None for anything else."""
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def normalize_tier(raw: str) -> str:
    tier = raw.strip().lower()
    return tier if tier in KNOWN_TIERS else "unknown"


def _read_rows(path: Path, expected_headers: list[str]) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != expected_headers:
            raise ValueError(
                f"{path.name}: unexpected headers {reader.fieldnames!r}; "
                f"expected {expected_headers!r}"
            )
        return list(reader)


def load_accounts(con: duckdb.DuckDBPyConnection, path: Path, report: IngestReport) -> None:
    seen_ids: set[int] = set()
    rows = []
    for raw in _read_rows(path, ACCOUNT_HEADERS):
        account_id = int(raw["Account Id"])
        if account_id in seen_ids:
            report.accounts_dropped += 1
            report.warn(
                f"accounts.csv: duplicate Account Id {account_id} "
                f"({raw['Account Name']!r}); kept first occurrence, dropped this row"
            )
            continue
        seen_ids.add(account_id)

        renewal = parse_vendor_date(raw["Renewal Dt"])
        if renewal is None:
            report.warn(
                f"accounts.csv: malformed Renewal Dt {raw['Renewal Dt']!r} for "
                f"{raw['Account Name']!r}; loaded with NULL renewal_date"
            )

        tier = normalize_tier(raw["Tier"])
        if tier == "unknown" and raw["Tier"].strip().lower() not in KNOWN_TIERS:
            report.warn(
                f"accounts.csv: unknown Tier {raw['Tier']!r} for "
                f"{raw['Account Name']!r}; coerced to 'unknown'"
            )

        rows.append(
            (
                account_id,
                raw["Account Name"].strip(),
                parse_money(raw["Annual Contract Value"]),
                tier,
                raw["Account Owner"].strip(),
                renewal,
                int(raw["Champion Contact Id"]),
                raw["Stage"].strip(),
            )
        )

    con.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    report.accounts_loaded = len(rows)


def load_contacts(con: duckdb.DuckDBPyConnection, path: Path, report: IngestReport) -> None:
    rows = []
    for raw in _read_rows(path, CONTACT_HEADERS):
        last_active = parse_vendor_date(raw["Last Activity"])
        if last_active is None:
            report.warn(
                f"contacts.csv: malformed Last Activity {raw['Last Activity']!r} "
                f"for {raw['Full Name']!r}; loaded as NULL"
            )
        rows.append(
            (
                int(raw["Contact Id"]),
                int(raw["Account Id"]),
                raw["Full Name"].strip(),
                raw["Job Title"].strip(),
                raw["Champion Flag"].strip().upper() == "Y",
                last_active,
            )
        )
    con.executemany("INSERT INTO contacts VALUES (?, ?, ?, ?, ?, ?)", rows)
    report.contacts_loaded = len(rows)


def load_usage_events(con: duckdb.DuckDBPyConnection, path: Path, report: IngestReport) -> None:
    rows = [
        (
            int(raw["Event Id"]),
            int(raw["Account Id"]),
            raw["Event"].strip(),
            datetime.strptime(raw["Timestamp"], "%Y-%m-%d %H:%M:%S"),
            float(raw["Weight"]),
        )
        for raw in _read_rows(path, EVENT_HEADERS)
    ]
    con.executemany("INSERT INTO usage_events VALUES (?, ?, ?, ?, ?)", rows)
    report.events_loaded = len(rows)


def load_all(con: duckdb.DuckDBPyConnection, raw_dir: Path) -> IngestReport:
    report = IngestReport()
    load_accounts(con, raw_dir / "accounts.csv", report)
    load_contacts(con, raw_dir / "contacts.csv", report)
    load_usage_events(con, raw_dir / "usage_events.csv", report)
    return report
