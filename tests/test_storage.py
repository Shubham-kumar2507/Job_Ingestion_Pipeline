from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models import JobListing
from src.storage import JobRow, initialize_database, make_engine, upsert_jobs


def test_sqlite_upsert_updates_instead_of_duplicating(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'jobs.db'}"
    initialize_database(database_url)
    original = JobListing(
        source="remoteok",
        external_id="42",
        title="Backend Engineer",
        company="Example",
        url="https://example.com/jobs/42",
    )
    revised = original.model_copy(update={"title": "Senior Backend Engineer"})

    assert upsert_jobs(database_url, [original]) == 1
    assert upsert_jobs(database_url, [revised]) == 1

    with Session(make_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(JobRow)) == 1
        assert session.scalar(select(JobRow.title)) == "Senior Backend Engineer"
