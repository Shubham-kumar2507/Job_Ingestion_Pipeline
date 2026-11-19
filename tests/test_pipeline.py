from src.models import JobListing
from src.pipeline import add_demo_invalid_record, jobs_to_persist


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


def test_demo_fixture_is_not_persisted():
    real_job = JobListing(
        source="remoteok",
        external_id="real-1",
        title="Engineer",
        company="Example",
        url="https://example.com/jobs/1",
    )
    demo_job = real_job.model_copy(
        update={
            "external_id": "demo-llm-repair-001",
            "source_payload": {"demo_fixture": True},
        }
    )

    assert jobs_to_persist([real_job, demo_job]) == [real_job]
