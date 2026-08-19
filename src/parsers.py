from collections.abc import Iterable
from typing import Any


def parse_remoteok(payload: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Source-specific adapter returning raw normalized dictionaries.

    RemoteOK's first list element is metadata (no ``id`` key), so records
    without an ``id`` are skipped.  Validation is deliberately kept separate
    in the pipeline layer — this function's job is field mapping only.
    """
    jobs: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("id"):
            continue

        salary_min = item.get("salary_min")
        salary_max = item.get("salary_max")

        # RemoteOK stores tags as a JSON list of strings.
        raw_tags = item.get("tags")
        tags = raw_tags if isinstance(raw_tags, list) else []

        jobs.append(
            {
                "source": "remoteok",
                "external_id": str(item["id"]),
                "title": item.get("position") or "",
                "company": item.get("company") or "",
                "url": item.get("url") or "",
                "description": item.get("description"),
                "location": item.get("location"),
                "employment_type": item.get("employment_type"),
                "salary_min": salary_min if isinstance(salary_min, int) else None,
                "salary_max": salary_max if isinstance(salary_max, int) else None,
                "posted_at": item.get("date"),
                "tags": tags,
                "source_payload": item,
            }
        )
    return jobs
