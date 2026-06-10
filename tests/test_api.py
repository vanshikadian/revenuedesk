"""Smoke tests for the dashboard endpoints. Offline, template narrator only."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REVOPS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REVOPS_DB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    # Re-import with patched env so config picks up the temp paths.
    import importlib

    from revops import api, config

    importlib.reload(config)
    importlib.reload(api)
    with TestClient(api.app) as test_client:
        yield test_client


def test_dashboard_renders_before_first_run(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Northwind Logistics" in response.text
    assert "UNSCORED" in response.text


def test_run_agent_flags_northwind_red(client):
    response = client.post("/agent/run")
    assert response.status_code == 200
    assert "SEV 3" in response.text
    assert "Northwind Logistics" in response.text
    assert "ARR at risk" in response.text
    # offline mode: the alert is logged, not sent
    assert "logged" in response.text

    # the dashboard reflects the persisted signals afterwards
    page = client.get("/")
    assert "SEV 3" in page.text
    assert "HEALTHY" in page.text


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
