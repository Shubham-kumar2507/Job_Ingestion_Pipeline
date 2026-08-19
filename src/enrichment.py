"""Lightweight post-validation enrichment: extract skills & normalize seniority.

This is a deterministic, zero-cost pass that runs on every validated listing —
no LLM calls. It demonstrates the optional stretch goal (normalize seniority,
extract skills, flag remote/hybrid) as a concrete differentiator.
"""

import re
from typing import Any

# Skills are matched case-insensitively against title + description + tags.
# Ordered roughly by market relevance; easily extensible.
SKILL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Python", re.compile(r"\bpython\b", re.I)),
    ("JavaScript", re.compile(r"\bjavascript\b|\bjs\b", re.I)),
    ("TypeScript", re.compile(r"\btypescript\b|\bts\b", re.I)),
    ("React", re.compile(r"\breact(?:\.?js)?\b", re.I)),
    ("Node.js", re.compile(r"\bnode(?:\.?js)?\b", re.I)),
    ("Go", re.compile(r"\bgolang\b|\bgo\b", re.I)),
    ("Rust", re.compile(r"\brust\b", re.I)),
    ("Java", re.compile(r"\bjava\b", re.I)),
    ("C++", re.compile(r"\bc\+\+\b|\bcpp\b", re.I)),
    ("Ruby", re.compile(r"\bruby\b", re.I)),
    ("PHP", re.compile(r"\bphp\b", re.I)),
    ("Swift", re.compile(r"\bswift\b", re.I)),
    ("Kotlin", re.compile(r"\bkotlin\b", re.I)),
    ("SQL", re.compile(r"\bsql\b|\bpostgres\b|\bmysql\b|\bsqlite\b", re.I)),
    ("AWS", re.compile(r"\baws\b|\bamazon web services\b", re.I)),
    ("GCP", re.compile(r"\bgcp\b|\bgoogle cloud\b", re.I)),
    ("Azure", re.compile(r"\bazure\b", re.I)),
    ("Docker", re.compile(r"\bdocker\b", re.I)),
    ("Kubernetes", re.compile(r"\bkubernetes\b|\bk8s\b", re.I)),
    ("Terraform", re.compile(r"\bterraform\b", re.I)),
    ("CI/CD", re.compile(r"\bci/?cd\b|\bgithub actions\b|\bjenkins\b", re.I)),
    ("Machine Learning", re.compile(r"\bmachine learning\b|\bml\b|\bdeep learning\b", re.I)),
    ("Data Engineering", re.compile(r"\bdata engineer\b|\betl\b|\bairflow\b|\bspark\b", re.I)),
    ("DevOps", re.compile(r"\bdevops\b|\bsre\b|\bsite reliability\b", re.I)),
    ("GraphQL", re.compile(r"\bgraphql\b", re.I)),
    ("Redis", re.compile(r"\bredis\b", re.I)),
    ("MongoDB", re.compile(r"\bmongodb\b|\bmongo\b", re.I)),
    ("Elasticsearch", re.compile(r"\belasticsearch\b|\belastic\b", re.I)),
    ("Linux", re.compile(r"\blinux\b", re.I)),
]

SENIORITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("intern", re.compile(r"\bintern(?:ship)?\b", re.I)),
    ("junior", re.compile(r"\bjunior\b|\bjr\.?\b|\bentry[\s-]?level\b", re.I)),
    ("mid", re.compile(r"\bmid[\s-]?level\b|\bmid[\s-]?senior\b", re.I)),
    ("senior", re.compile(r"\bsenior\b|\bsr\.?\b", re.I)),
    ("staff", re.compile(r"\bstaff\b", re.I)),
    ("principal", re.compile(r"\bprincipal\b", re.I)),
    ("lead", re.compile(r"\blead\b|\bteam lead\b", re.I)),
    ("manager", re.compile(r"\bmanager\b|\bengineering manager\b|\bem\b", re.I)),
    ("director", re.compile(r"\bdirector\b", re.I)),
    ("vp", re.compile(r"\bvp\b|\bvice president\b", re.I)),
    ("c-level", re.compile(r"\bcto\b|\bceo\b|\bcoo\b|\bc-level\b", re.I)),
]

WORK_MODEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("remote", re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b", re.I)),
    ("hybrid", re.compile(r"\bhybrid\b", re.I)),
    ("onsite", re.compile(r"\bon[\s-]?site\b|\bin[\s-]?office\b", re.I)),
]


def _build_corpus(title: str, description: str | None, tags: list[str]) -> str:
    """Combine searchable fields into one lowercase corpus."""
    parts = [title]
    if description:
        parts.append(description[:3000])  # cap to avoid scanning massive HTML blobs
    if tags:
        parts.append(" ".join(tags))
    return " ".join(parts)


def extract_skills(corpus: str) -> list[str]:
    """Return deduplicated skill names found in the text corpus."""
    return [name for name, pattern in SKILL_PATTERNS if pattern.search(corpus)]


def infer_seniority(corpus: str) -> str | None:
    """Return the first matched seniority level, or None."""
    for label, pattern in SENIORITY_PATTERNS:
        if pattern.search(corpus):
            return label
    return None


def infer_work_model(corpus: str) -> str | None:
    """Return remote/hybrid/onsite, or None if indeterminate."""
    for label, pattern in WORK_MODEL_PATTERNS:
        if pattern.search(corpus):
            return label
    return None


def enrich(job_dict: dict[str, Any]) -> dict[str, Any]:
    """Add extracted_skills, seniority, and work_model to a validated job dict.

    This mutates and returns the same dict for pipeline convenience.
    """
    corpus = _build_corpus(
        job_dict.get("title", ""),
        job_dict.get("description"),
        job_dict.get("tags", []),
    )
    job_dict["extracted_skills"] = extract_skills(corpus)
    job_dict["seniority"] = infer_seniority(corpus)
    job_dict["work_model"] = infer_work_model(corpus)
    return job_dict
