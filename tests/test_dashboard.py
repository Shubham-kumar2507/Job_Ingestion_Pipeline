from fastapi.testclient import TestClient

import src.dashboard as dashboard
from src.storage import finish_run, initialize_database, record_llm_repair, start_run


def test_vercel_uses_writable_temporary_sqlite_database(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert dashboard._default_database_url() == "sqlite:////tmp/job_listings.db"


def test_dashboard_labels_postgresql_backend(monkeypatch):
    monkeypatch.setattr(dashboard, "DATABASE_URL", "postgresql://user:password@localhost/jobs")
    monkeypatch.setattr(dashboard, "initialize_database", lambda _: None)
    monkeypatch.setattr(dashboard, "dashboard_data", lambda *_args, **_kwargs: ([], None, 0, [], 0))

    response = TestClient(dashboard.app).get("/")

    assert response.status_code == 200
    assert "PostgreSQL upsert" in response.text
    assert "Built with FastAPI + PostgreSQL" in response.text


def test_dashboard_renders_clickable_llm_repair_details(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'dashboard.db'}"
    monkeypatch.setattr(dashboard, "DATABASE_URL", database_url)
    initialize_database(database_url)
    run_id = start_run(database_url, "remoteok")
    record_llm_repair(
        database_url,
        run_id=run_id,
        external_id="repair-42",
        model="openai/gpt-oss-20b",
        outcome="repaired",
        validation_error="title must not be blank",
        repaired_fields=["title", "company"],
    )
    finish_run(database_url, run_id, status="success", llm_calls_used=1)

    response = TestClient(dashboard.app).get("/")

    assert response.status_code == 200
    assert "View LLM work" in response.text
    assert 'aria-haspopup="dialog"' in response.text
    assert "LLM repair details" in response.text
    assert "title must not be blank" in response.text
    assert "openai/gpt-oss-20b" in response.text
