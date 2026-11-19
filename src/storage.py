from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .models import JobListing


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "job_listings"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(500))
    employment_type: Mapped[str | None] = mapped_column(String(100))
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    extracted_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    seniority: Mapped[str | None] = mapped_column(String(50))
    work_model: Mapped[str | None] = mapped_column(String(50))
    source_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class IngestionRunRow(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)


class LLMRepairRow(Base):
    """Auditable summary of one real LLM fallback attempt."""

    __tablename__ = "llm_repairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    validation_error: Mapped[str] = mapped_column(Text, nullable=False)
    repaired_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


def make_engine(database_url: str):
    url = make_url(database_url)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    return create_engine(url, pool_pre_ping=True)


def initialize_database(database_url: str) -> None:
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    # ``create_all`` does not alter an existing SQLite table. Additive
    # migrations keep older databases compatible with the current schema.
    if database_url.startswith("sqlite"):
        with engine.begin() as connection:
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(job_listings)")}
            migrations = [
                ("tags", "ALTER TABLE job_listings ADD COLUMN tags JSON NOT NULL DEFAULT '[]'"),
                ("extracted_skills", "ALTER TABLE job_listings ADD COLUMN extracted_skills JSON NOT NULL DEFAULT '[]'"),
                ("seniority", "ALTER TABLE job_listings ADD COLUMN seniority VARCHAR(50)"),
                ("work_model", "ALTER TABLE job_listings ADD COLUMN work_model VARCHAR(50)"),
            ]
            for col_name, ddl in migrations:
                if col_name not in columns:
                    connection.exec_driver_sql(ddl)


def start_run(database_url: str, source: str) -> int:
    engine = make_engine(database_url)
    with Session(engine) as session:
        run = IngestionRunRow(source=source, started_at=datetime.now(timezone.utc), status="running")
        session.add(run)
        session.commit()
        return run.id


def finish_run(
    database_url: str,
    run_id: int,
    *,
    status: str,
    fetched_count: int = 0,
    valid_count: int = 0,
    stored_count: int = 0,
    invalid_count: int = 0,
    llm_calls_used: int = 0,
    error_message: str | None = None,
) -> None:
    engine = make_engine(database_url)
    with Session(engine) as session:
        run = session.get(IngestionRunRow, run_id)
        if run is None:
            raise ValueError(f"Unknown ingestion run {run_id}")
        run.finished_at = datetime.now(timezone.utc)
        run.status = status
        run.fetched_count = fetched_count
        run.valid_count = valid_count
        run.stored_count = stored_count
        run.invalid_count = invalid_count
        run.llm_calls_used = llm_calls_used
        run.error_message = error_message
        session.commit()


def record_llm_repair(
    database_url: str,
    *,
    run_id: int,
    external_id: str,
    model: str | None,
    outcome: str,
    validation_error: str,
    repaired_fields: list[str] | None = None,
) -> None:
    engine = make_engine(database_url)
    with Session(engine) as session:
        session.add(LLMRepairRow(
            run_id=run_id,
            external_id=external_id,
            model=model,
            outcome=outcome,
            validation_error=validation_error[:1000],
            repaired_fields=repaired_fields or [],
            created_at=datetime.now(timezone.utc),
        ))
        session.commit()


def dashboard_data(database_url: str, limit: int = 20) -> tuple[list[JobRow], IngestionRunRow | None, int, list[LLMRepairRow], int]:
    """Return recent listings, the last completed run, streak, repairs, and total count."""
    engine = make_engine(database_url)
    with Session(engine) as session:
        from sqlalchemy import func
        total_listings = session.scalar(select(func.count()).select_from(JobRow)) or 0
        jobs = list(session.scalars(select(JobRow).order_by(JobRow.posted_at.desc()).limit(limit)))
        runs = list(session.scalars(
            select(IngestionRunRow)
            .where(IngestionRunRow.status.in_(("success", "failed")))
            .order_by(IngestionRunRow.finished_at.desc())
        ))
        last_run = runs[0] if runs else None
        streak = 0
        if last_run:
            for run in runs:
                if run.status != last_run.status:
                    break
                streak += 1
        repairs = list(session.scalars(
            select(LLMRepairRow)
            .where(LLMRepairRow.run_id == last_run.id)
            .order_by(LLMRepairRow.id.asc())
        )) if last_run else []
        # Detach simple ORM objects before the session closes for template use.
        for record in [*jobs, *runs[:1], *repairs]:
            session.expunge(record)
        return jobs, last_run, streak, repairs, total_listings


def upsert_jobs(database_url: str, jobs: list[JobListing]) -> int:
    """Insert new jobs and update existing records using the database's unique key."""
    if not jobs:
        return 0
    engine = make_engine(database_url)
    if engine.dialect.name == "sqlite":
        insert_statement = sqlite_insert
    elif engine.dialect.name == "postgresql":
        insert_statement = postgresql_insert
    else:
        raise ValueError(f"Unsupported database dialect for job upserts: {engine.dialect.name}")
    now = datetime.now(timezone.utc)
    rows = [
        {
            **job.model_dump(mode="python"),
            "url": str(job.url),
            "updated_at": now,
        }
        for job in jobs
    ]
    statement = insert_statement(JobRow).values(rows)
    update_columns = {
        column: statement.excluded[column]
        for column in (
            "title", "company", "url", "description", "location", "employment_type",
            "salary_min", "salary_max", "posted_at", "tags", "extracted_skills",
            "seniority", "work_model", "source_payload", "updated_at",
        )
    }
    statement = statement.on_conflict_do_update(
        index_elements=("source", "external_id"),
        set_=update_columns,
    )
    with Session(engine) as session:
        session.execute(statement)
        session.commit()
    return len(jobs)
