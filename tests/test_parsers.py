"""Tests for the RemoteOK parser, Pydantic validation, and circuit breaker."""

import asyncio

import pytest
from pydantic import ValidationError

from src.fetch import CircuitBreaker
from src.models import JobListing
from src.parsers import parse_remoteok


# ──────────────────────────────────────────────
# Parser tests
# ──────────────────────────────────────────────

def test_parse_remoteok_skips_metadata_and_maps_job():
    """First element (metadata) should be skipped; valid records should map correctly."""
    jobs = parse_remoteok([
        {"legal": "metadata"},
        {
            "id": "42", "position": "Backend Engineer", "company": "Example",
            "url": "https://example.com/jobs/42", "tags": ["python", "aws"],
        },
    ])
    assert len(jobs) == 1
    assert jobs[0]["source"] == "remoteok"
    assert jobs[0]["external_id"] == "42"
    assert jobs[0]["title"] == "Backend Engineer"
    assert jobs[0]["company"] == "Example"
    assert jobs[0]["url"] == "https://example.com/jobs/42"
    assert jobs[0]["tags"] == ["python", "aws"]
    assert jobs[0]["description"] is None
    assert jobs[0]["location"] is None


def test_parse_remoteok_skips_items_without_id():
    """Items without an 'id' key (including the metadata record) are silently skipped."""
    jobs = parse_remoteok([
        {"legal": "metadata"},
        {"position": "No ID Job"},
        {"id": "", "position": "Empty ID Job"},
    ])
    assert jobs == []


def test_parse_remoteok_handles_empty_payload():
    """An empty payload returns an empty list, not an error."""
    assert parse_remoteok([]) == []


def test_parse_remoteok_coerces_salary_types():
    """Non-integer salary values should be coerced to None by the parser."""
    jobs = parse_remoteok([
        {
            "id": "99", "position": "Dev", "company": "Co",
            "url": "https://example.com", "salary_min": "50000", "salary_max": 100000,
        },
    ])
    assert jobs[0]["salary_min"] is None  # string, not int
    assert jobs[0]["salary_max"] == 100000


def test_parse_remoteok_tags_fallback_for_non_list():
    """If tags is not a list, the parser falls back to an empty list."""
    jobs = parse_remoteok([
        {"id": "1", "position": "Dev", "company": "Co", "url": "https://x.com", "tags": "not-a-list"},
    ])
    assert jobs[0]["tags"] == []


# ──────────────────────────────────────────────
# Pydantic model validation tests
# ──────────────────────────────────────────────

def test_valid_job_listing():
    """A complete record validates without errors."""
    job = JobListing(
        source="remoteok",
        external_id="1",
        title="Engineer",
        company="Acme",
        url="https://example.com/jobs/1",
    )
    assert job.source == "remoteok"
    assert str(job.url) == "https://example.com/jobs/1"


def test_blank_required_field_fails():
    """Blank required fields should raise a ValidationError, not silently pass."""
    with pytest.raises(ValidationError):
        JobListing(
            source="remoteok", external_id="1", title="", company="Acme",
            url="https://example.com",
        )


def test_missing_url_fails():
    """Missing URL should fail validation."""
    with pytest.raises(ValidationError):
        JobListing(source="remoteok", external_id="1", title="Dev", company="Acme", url="")


def test_optional_fields_default_to_none():
    """Optional fields should not be required and should default to None."""
    job = JobListing(
        source="remoteok", external_id="1", title="Dev", company="Acme",
        url="https://example.com",
    )
    assert job.description is None
    assert job.location is None
    assert job.salary_min is None
    assert job.tags == []


def test_extra_fields_are_ignored():
    """Unknown fields in the source data should be silently ignored."""
    job = JobListing(
        source="remoteok", external_id="1", title="Dev", company="Acme",
        url="https://example.com", unknown_field="surprise",
    )
    assert not hasattr(job, "unknown_field")


# ──────────────────────────────────────────────
# Circuit breaker tests
# ──────────────────────────────────────────────

def test_circuit_breaker_stays_closed_below_threshold():
    """Below the failure threshold, before_request should not pause."""
    breaker = CircuitBreaker(failure_threshold=3)
    assert not breaker.is_open
    breaker.record_failure("test", Exception("err"))
    breaker.record_failure("test", Exception("err"))
    assert breaker.consecutive_failures == 2
    assert not breaker.is_open


def test_circuit_breaker_opens_at_threshold():
    """At the threshold, the breaker should be open."""
    breaker = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        breaker.record_failure("test", Exception("err"))
    assert breaker.is_open
    assert breaker.consecutive_failures == 3


def test_circuit_breaker_resets_on_success():
    """A single success should reset the breaker to closed."""
    breaker = CircuitBreaker(failure_threshold=2)
    breaker.record_failure("test", Exception("err"))
    breaker.record_failure("test", Exception("err"))
    assert breaker.is_open
    breaker.record_success()
    assert not breaker.is_open
    assert breaker.consecutive_failures == 0
