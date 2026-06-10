"""FastAPI app serving the dashboard and the htmx agent-run endpoint."""

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

import duckdb
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from revops import agent, config, db, seed
from revops.signals import SIGNAL_LABELS

log = logging.getLogger(__name__)

_con: duckdb.DuckDBPyConnection | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _con
    # Surface app logs (e.g. the offline Slack payload) in the uvicorn terminal.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _con = db.connect()
    accounts = _con.execute("SELECT count(*) FROM accounts").fetchone()[0]
    if accounts == 0:
        log.info("Empty warehouse, seeding demo data")
        _con.close()
        seed.seed()
        _con = db.connect()
    yield
    _con.close()


app = FastAPI(title="RevenueDesk", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def cursor() -> duckdb.DuckDBPyConnection:
    """Per-request cursor; DuckDB connections aren't shareable across threads."""
    assert _con is not None
    return _con.cursor()


def _load_board(con: duckdb.DuckDBPyConnection) -> dict:
    """Everything the board needs: accounts ranked by risk, traces, sparklines."""
    today = date.today()
    accounts = con.execute(
        """
        SELECT a.id, a.name, a.arr, a.tier, a.owner_rep, a.renewal_date,
               c.name AS champion, c.last_active_date,
               COALESCE(MAX(s.severity), 0) AS severity
        FROM accounts a
        LEFT JOIN contacts c ON c.id = a.champion_contact_id
        LEFT JOIN account_signals s ON s.account_id = a.id
        GROUP BY ALL
        ORDER BY severity DESC, a.arr DESC
        """
    ).fetchall()

    signal_rows = con.execute(
        "SELECT account_id, signal, severity, recommended_action, reason FROM account_signals"
    ).fetchall()
    traces: dict[int, list[dict]] = {}
    for account_id, signal, severity, action, reason in signal_rows:
        traces.setdefault(account_id, []).append(
            {
                "signal": signal,
                "label": SIGNAL_LABELS.get(signal, signal),
                "severity": severity,
                "action": action,
                "reason": reason,
            }
        )

    series_rows = con.execute(
        """
        SELECT account_id, CAST(ts AS DATE) AS day, SUM(weight)
        FROM usage_events GROUP BY 1, 2 ORDER BY 1, 2
        """
    ).fetchall()
    days = [
        today.fromordinal(today.toordinal() - offset)
        for offset in range(config.USAGE_HISTORY_DAYS - 1, -1, -1)
    ]
    by_account: dict[int, dict] = {}
    for account_id, day, total in series_rows:
        by_account.setdefault(account_id, {})[day] = round(total, 1)

    board = []
    for row in accounts:
        (account_id, name, arr, tier, owner, renewal, champion, champ_active, severity) = row
        account_traces = sorted(
            traces.get(account_id, []), key=lambda t: (-t["severity"], t["label"])
        )
        daily = by_account.get(account_id, {})
        board.append(
            {
                "id": account_id,
                "name": name,
                "arr": arr,
                "tier": tier,
                "owner": owner,
                "renewal_date": renewal,
                "days_until_renewal": (renewal - today).days if renewal else None,
                "champion": champion,
                "champion_silent_days": (today - champ_active).days if champ_active else None,
                "severity": severity,
                "traces": account_traces,
                "series": [daily.get(d, 0) for d in days],
            }
        )

    last_run = con.execute(
        "SELECT run_at, summary, accounts_flagged, arr_at_risk "
        "FROM agent_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()

    flagged = [a for a in board if a["severity"] > 0]
    return {
        "accounts": board,
        "accounts_watched": len(board),
        "accounts_flagged": len(flagged),
        "arr_at_risk": sum(a["arr"] for a in flagged),
        "summary": last_run[1] if last_run else None,
        "last_run_at": last_run[0] if last_run else None,
        "slack_configured": config.slack_webhook_url() is not None,
        "has_run": last_run is not None,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    con = cursor()
    try:
        ctx = _load_board(con)
    finally:
        con.close()
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.post("/agent/run", response_class=HTMLResponse)
def agent_run(request: Request):
    """Run the full agent loop and return the re-rendered board + summary."""
    con = cursor()
    try:
        result = agent.run_once(con=con)
        ctx = _load_board(con)
    finally:
        con.close()
    ctx.update(
        {
            "just_ran": True,
            "narrator_name": result.narrator_name,
            "alerts_sent": result.alerts_sent,
            "alerts_logged": result.alerts_logged,
        }
    )
    return templates.TemplateResponse(request, "partials/run_response.html", ctx)


@app.get("/health")
def health():
    return {"status": "ok", "run_at": datetime.now().isoformat()}
