import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from pydantic import ValidationError

from .enrichment import enrich
from .fetch import fetch_remoteok
from .llm_fallback import LLMFallback
from .models import JobListing
from .parsers import parse_remoteok
from .storage import finish_run, initialize_database, record_llm_repair, start_run, upsert_jobs

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def log_event(event: str, **fields: object) -> None:
    logger.info(json.dumps({"event": event, **fields}, default=str))


def log_error(event: str, **fields: object) -> None:
    logger.error(json.dumps({"event": event, **fields}, default=str))


def add_demo_invalid_record(raw_jobs: list[dict]) -> list[dict]:
    """Append a labelled fixture to demonstrate the LLM repair path.

    This never changes RemoteOK's response and is disabled by default. The
    fixture intentionally omits title and company, while its description has
    both values, making the deterministic validator fail and the LLM fallback
    demonstrable.
    """
    if os.getenv("DEMO_INJECT_INVALID_RECORD", "false").lower() != "true":
        return raw_jobs
    demo_record = {
        "source": "remoteok",
        "external_id": "demo-llm-repair-001",
        "title": "",
        "company": "",
        "url": "https://example.invalid/demo-llm-repair-001",
        "description": "Title: AI Automation Engineer. Company: Example Labs. Fully remote.",
        "location": "Remote",
        "employment_type": None,
        "salary_min": None,
        "salary_max": None,
        "posted_at": None,
        "tags": ["demo"],
        "source_payload": {
            "demo_fixture": True,
            "raw_text": "Title: AI Automation Engineer. Company: Example Labs.",
        },
    }
    log_event("demo_invalid_record_injected", external_id=demo_record["external_id"])
    return [*raw_jobs, demo_record]


async def run() -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required; copy .env.example to .env and configure it")

    initialize_database(database_url)
    run_id = start_run(database_url, "remoteok")
    payload: list[dict] = []
    valid_jobs: list[JobListing] = []
    invalid_count = 0
    enriched_count = 0
    llm_fallback = LLMFallback.from_environment()
    try:
        payload = await fetch_remoteok()
        raw_jobs = add_demo_invalid_record(parse_remoteok(payload))
        log_event("parse_complete", source="remoteok", raw_count=len(raw_jobs), payload_count=len(payload))

        for raw_job in raw_jobs:
            try:
                validated_job = JobListing.model_validate(raw_job)
                enriched = enrich(validated_job.model_dump(mode="python"))
                valid_jobs.append(JobListing.model_validate(enriched))
                enriched_count += 1
            except ValidationError as error:
                log_error(
                    "job_validation_failed",
                    source="remoteok",
                    external_id=raw_job.get("external_id"),
                    error=error.errors(),
                )
                # Attempt LLM repair only for validation failures.
                calls_before = llm_fallback.calls_used
                repaired_fields = await llm_fallback.repair(raw_job, str(error))
                fallback_was_called = llm_fallback.calls_used > calls_before
                if repaired_fields:
                    repaired_job = {
                        **raw_job,
                        **{k: v for k, v in repaired_fields.items() if v is not None},
                    }
                    try:
                        repaired_job = JobListing.model_validate(repaired_job)
                        enriched_repair = enrich(repaired_job.model_dump(mode="python"))
                        valid_jobs.append(JobListing.model_validate(enriched_repair))
                        enriched_count += 1
                        log_event(
                            "llm_fallback_validation_succeeded",
                            source="remoteok",
                            external_id=raw_job.get("external_id"),
                        )
                        if fallback_was_called:
                            record_llm_repair(
                                database_url, run_id=run_id, external_id=str(raw_job.get("external_id")),
                                model=llm_fallback.model, outcome="repaired",
                                validation_error=str(error), repaired_fields=sorted(repaired_fields),
                            )
                        continue
                    except ValidationError as repair_error:
                        log_error(
                            "llm_fallback_validation_failed",
                            source="remoteok",
                            external_id=raw_job.get("external_id"),
                            error=repair_error.errors(),
                        )
                        if fallback_was_called:
                            record_llm_repair(
                                database_url, run_id=run_id, external_id=str(raw_job.get("external_id")),
                                model=llm_fallback.model, outcome="still_invalid",
                                validation_error=str(repair_error), repaired_fields=sorted(repaired_fields),
                            )
                elif fallback_was_called:
                    record_llm_repair(
                        database_url, run_id=run_id, external_id=str(raw_job.get("external_id")),
                        model=llm_fallback.model, outcome="no_repair_returned",
                        validation_error=str(error),
                    )
                invalid_count += 1
            except Exception as error:
                # Catch-all keeps the batch loop alive instead of crashing the
                # entire pipeline on one malformed record.
                invalid_count += 1
                log_error(
                    "job_processing_failed",
                    source="remoteok",
                    external_id=raw_job.get("external_id"),
                    error=str(error),
                )

        stored = upsert_jobs(database_url, valid_jobs)
        finish_run(
            database_url,
            run_id,
            status="success",
            fetched_count=len(payload),
            valid_count=len(valid_jobs),
            stored_count=stored,
            invalid_count=invalid_count,
            llm_calls_used=llm_fallback.calls_used,
        )
        log_event(
            "ingestion_run_complete",
            source="remoteok",
            fetched=len(payload),
            validated=len(valid_jobs),
            enriched=enriched_count,
            stored=stored,
            invalid=invalid_count,
            llm_calls=llm_fallback.calls_used,
        )
        print(
            f"Fetched {len(payload)} payload items; validated {len(valid_jobs)} jobs; "
            f"enriched {enriched_count}; upserted {stored} rows; {invalid_count} invalid; "
            f"{llm_fallback.calls_used} LLM calls."
        )
    except Exception as error:
        finish_run(
            database_url,
            run_id,
            status="failed",
            fetched_count=len(payload),
            valid_count=len(valid_jobs),
            invalid_count=invalid_count,
            llm_calls_used=llm_fallback.calls_used,
            error_message=str(error),
        )
        log_error("ingestion_run_failed", source="remoteok", run_id=run_id, error=str(error))
        raise


if __name__ == "__main__":
    asyncio.run(run())
