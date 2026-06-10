"""Deterministic demo-data generator.

Writes vendor-style CSVs into data/raw/, then ingests them through the
cleaning layer. A fixed random seed keeps the data identical across runs.

The generated data includes:

* Northwind Logistics (~$250K enterprise) has both a sharp usage drop over
  the trailing 14 days and a champion who went silent 45 days ago, which
  should score severity 3.
* Acme Manufacturing has only an upcoming renewal (~30 days out).
* Bluebird Media has only a quiet champion (38 days silent).
* Helios Energy has only a usage drop (high ARR, so severity 2).
* Everything else is a healthy control.

Three rows are intentionally malformed to exercise ingestion: a bad renewal
date, an unknown tier value, and a duplicate account id.
"""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from revops import config, db, loaders

EVENT_TYPES = ["api_call", "login", "report_run", "export", "admin_action"]


@dataclass
class AccountSpec:
    id: int
    name: str
    arr: int
    tier: str  # vendor-cased, e.g. "Enterprise"
    owner: str
    renewal_in_days: int | None
    stage: str = "live"
    usage: str = "healthy"  # "healthy" | "drop"
    champion_silent_days: int | None = None  # None -> recently active
    renewal_raw: str | None = None  # override to plant malformed values
    contacts: list[tuple[str, str]] = field(default_factory=list)  # (name, title)


SPECS = [
    AccountSpec(
        1,
        "Northwind Logistics",
        250_000,
        "Enterprise",
        "Maya Chen",
        150,
        usage="drop",
        champion_silent_days=45,
        contacts=[
            ("Priya Nair", "VP Operations"),
            ("Tom Walsh", "Logistics Analyst"),
            ("Dana Reeve", "IT Director"),
        ],
    ),
    AccountSpec(
        2,
        "Acme Manufacturing",
        85_000,
        "Growth",
        "Jordan Lee",
        30,
        contacts=[("Luis Romero", "Plant Manager"), ("Faye Donnelly", "Ops Lead")],
    ),
    AccountSpec(
        3,
        "Bluebird Media",
        64_000,
        "Growth",
        "Maya Chen",
        200,
        champion_silent_days=38,
        contacts=[("Greta Solberg", "Head of Audience"), ("Omar Haddad", "Data Analyst")],
    ),
    AccountSpec(
        4,
        "Helios Energy",
        120_000,
        "Growth",
        "Sam Ortiz",
        180,
        usage="drop",
        contacts=[("Ingrid Falk", "Director of Ops"), ("Ben Castor", "Grid Engineer")],
    ),
    AccountSpec(
        5,
        "Cascade Software",
        95_000,
        "Growth",
        "Jordan Lee",
        160,
        contacts=[("Nina Petrov", "CTO"), ("Will Sato", "Platform Eng")],
    ),
    AccountSpec(
        6,
        "Ironwood Construction",
        45_000,
        "Starter",
        "Sam Ortiz",
        220,
        contacts=[("Earl Jensen", "Site Director"), ("Rosa Imran", "Scheduler")],
    ),
    AccountSpec(
        7,
        "Lumen Analytics",
        150_000,
        "Enterprise",
        "Maya Chen",
        260,
        contacts=[("Aldo Brandt", "Chief Data Officer"), ("June Park", "Analyst")],
    ),
    AccountSpec(
        8,
        "Pacific Crest Bank",
        180_000,
        "Enterprise",
        "Jordan Lee",
        300,
        contacts=[("Hannah Yoon", "VP Technology"), ("Marcus Bell", "Risk Lead")],
    ),
    AccountSpec(
        9,
        "Sable & Finch",
        38_000,
        "Starter",
        "Sam Ortiz",
        140,
        contacts=[("Clara Finch", "Founder"), ("Devon Ames", "Ops Manager")],
    ),
    AccountSpec(
        10,
        "Tundra Foods",
        52_000,
        "Starter",
        "Jordan Lee",
        190,
        contacts=[("Pete Kowalski", "Supply Chain Mgr"), ("Lena Voss", "Buyer")],
    ),
    AccountSpec(
        11,
        "Veridian Health",
        110_000,
        "Enterprise",
        "Sam Ortiz",
        240,
        contacts=[("Dr. Asha Patel", "CMIO"), ("Ray Donnelly", "IT Manager")],
    ),
    AccountSpec(
        12,
        "Westgate Travel",
        70_000,
        "Growth",
        "Maya Chen",
        170,
        contacts=[("Sofia Marques", "Head of Product"), ("Theo Lindqvist", "PM")],
    ),
    # --- intentionally messy rows ---------------------------------------------
    AccountSpec(
        13,
        "Zenith Retail",
        58_000,
        "Growth",
        "Sam Ortiz",
        None,
        renewal_raw="TBD",  # malformed date -> should load with NULL renewal
        contacts=[("Vera Lindo", "Ecommerce Lead"), ("Kit Tanaka", "Merchandiser")],
    ),
    AccountSpec(
        14,
        "Orchid Health Labs",
        66_000,
        "Platinum++",
        "Jordan Lee",
        210,
        # unknown tier -> should be coerced to "unknown"
        contacts=[("Mia Strand", "Lab Director"), ("Joel Abara", "Research Ops")],
    ),
]


def _money(n: int) -> str:
    return f"${n:,}"


def _vdate(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def write_csvs(raw_dir: Path | None = None, today: date | None = None) -> Path:
    """Generate the vendor CSV exports. Returns the directory written to."""
    raw_dir = Path(raw_dir or config.RAW_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    rng = random.Random(config.RANDOM_SEED)

    account_rows: list[list[str]] = []
    contact_rows: list[list[str]] = []
    event_rows: list[list[str]] = []
    contact_id = 100
    event_id = 1

    for spec in SPECS:
        renewal = (
            spec.renewal_raw
            if spec.renewal_raw is not None
            else _vdate(today + timedelta(days=spec.renewal_in_days))
        )
        champion_id = contact_id  # first contact is the champion
        account_rows.append(
            [
                str(spec.id),
                spec.name,
                _money(spec.arr),
                spec.tier,
                spec.owner,
                renewal,
                str(champion_id),
                spec.stage,
            ]
        )

        for idx, (cname, ctitle) in enumerate(spec.contacts):
            is_champion = idx == 0
            if is_champion and spec.champion_silent_days is not None:
                last_active = today - timedelta(days=spec.champion_silent_days)
            else:
                last_active = today - timedelta(days=rng.randint(1, 12))
            contact_rows.append(
                [
                    str(contact_id),
                    str(spec.id),
                    cname,
                    ctitle,
                    "Y" if is_champion else "N",
                    _vdate(last_active),
                ]
            )
            contact_id += 1

        # 28 days of usage. "drop" profiles fall off a cliff in the last 14 days.
        for days_ago in range(config.USAGE_HISTORY_DAYS - 1, -1, -1):
            day = today - timedelta(days=days_ago)
            if spec.usage == "drop" and days_ago < config.USAGE_WINDOW_DAYS:
                n_events = rng.randint(2, 4)  # ~70% below the prior window
            elif spec.usage == "drop":
                n_events = rng.randint(10, 16)
            else:
                n_events = rng.randint(6, 12)
            for _ in range(n_events):
                ts = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=rng.randint(8, 19), minutes=rng.randint(0, 59)
                )
                event_rows.append(
                    [
                        str(event_id),
                        str(spec.id),
                        rng.choice(EVENT_TYPES),
                        ts.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{rng.uniform(0.5, 3.0):.2f}",
                    ]
                )
                event_id += 1

    # A duplicate account id, as seen in real CRM exports.
    account_rows.append(
        [
            "1",
            "Northwind Logistics (dupe)",
            "$250,000",
            "Enterprise",
            "Maya Chen",
            _vdate(today + timedelta(days=150)),
            "100",
            "live",
        ]
    )

    _write_csv(raw_dir / "accounts.csv", loaders.ACCOUNT_HEADERS, account_rows)
    _write_csv(raw_dir / "contacts.csv", loaders.CONTACT_HEADERS, contact_rows)
    _write_csv(raw_dir / "usage_events.csv", loaders.EVENT_HEADERS, event_rows)
    return raw_dir


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    import csv

    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


def seed(db_path: str | Path | None = None, today: date | None = None) -> loaders.IngestReport:
    """Generate CSVs and load them into a fresh warehouse."""
    raw_dir = write_csvs(today=today)
    con = db.connect(db_path)
    try:
        db.reset(con)
        return loaders.load_all(con, raw_dir)
    finally:
        con.close()


def main() -> None:
    import sys

    import duckdb

    try:
        report = seed()
    except duckdb.IOException as exc:
        if "lock" in str(exc).lower():
            sys.exit(
                "The warehouse is locked by a running server (DuckDB is "
                "single-writer).\nStop `make demo` / `make serve` first, then re-run "
                "`make seed`."
            )
        raise
    print(f"Seeded warehouse at {config.DB_PATH}")
    print(report.pretty())


if __name__ == "__main__":
    main()
