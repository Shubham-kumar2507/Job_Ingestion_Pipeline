from src.pipeline import add_demo_invalid_record


def test_demo_fixture_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DEMO_INJECT_INVALID_RECORD", raising=False)
    jobs = [{"external_id": "real"}]
    assert add_demo_invalid_record(jobs) == jobs


def test_demo_fixture_adds_one_invalid_record(monkeypatch):
    monkeypatch.setenv("DEMO_INJECT_INVALID_RECORD", "true")
    jobs = add_demo_invalid_record([])
    assert len(jobs) == 1
    assert jobs[0]["external_id"] == "demo-llm-repair-001"
    assert jobs[0]["title"] == ""
    assert "Title: AI Automation Engineer" in jobs[0]["description"]
    assert "Company: Example Labs" in jobs[0]["description"]
