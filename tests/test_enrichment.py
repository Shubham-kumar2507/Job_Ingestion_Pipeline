"""Tests for the enrichment module: skill extraction, seniority inference, work model."""

from src.enrichment import enrich, extract_skills, infer_seniority, infer_work_model


# ──────────────────────────────────────────────
# Skill extraction
# ──────────────────────────────────────────────

def test_extract_skills_finds_python_and_aws():
    corpus = "We need a Python developer with AWS experience"
    skills = extract_skills(corpus)
    assert "Python" in skills
    assert "AWS" in skills


def test_extract_skills_case_insensitive():
    corpus = "JAVASCRIPT and DOCKER required"
    skills = extract_skills(corpus)
    assert "JavaScript" in skills
    assert "Docker" in skills


def test_extract_skills_empty_corpus():
    assert extract_skills("") == []


def test_extract_skills_no_matches():
    assert extract_skills("This is a generic role with no tech mentioned") == []


def test_extract_skills_deduplicates():
    corpus = "Python python PYTHON"
    skills = extract_skills(corpus)
    assert skills.count("Python") == 1


# ──────────────────────────────────────────────
# Seniority inference
# ──────────────────────────────────────────────

def test_infer_seniority_senior():
    assert infer_seniority("Senior Backend Engineer") == "senior"


def test_infer_seniority_junior():
    assert infer_seniority("Junior Developer position") == "junior"


def test_infer_seniority_lead():
    assert infer_seniority("Engineering Lead") == "lead"


def test_infer_seniority_staff():
    assert infer_seniority("Staff Software Engineer") == "staff"


def test_infer_seniority_none_when_absent():
    assert infer_seniority("Software Engineer") is None


def test_infer_seniority_intern():
    assert infer_seniority("Summer Internship Program") == "intern"


# ──────────────────────────────────────────────
# Work model inference
# ──────────────────────────────────────────────

def test_infer_work_model_remote():
    assert infer_work_model("Fully remote position, work from anywhere") == "remote"


def test_infer_work_model_hybrid():
    assert infer_work_model("Hybrid role — 3 days in office") == "hybrid"


def test_infer_work_model_onsite():
    assert infer_work_model("On-site in San Francisco") == "onsite"


def test_infer_work_model_none_when_absent():
    assert infer_work_model("Software Engineer at Acme Corp") is None


# ──────────────────────────────────────────────
# Full enrich() integration
# ──────────────────────────────────────────────

def test_enrich_adds_all_fields():
    job = {
        "title": "Senior Python Developer",
        "description": "Build AWS infrastructure with Docker and Kubernetes",
        "tags": ["python", "devops"],
    }
    result = enrich(job)
    assert "extracted_skills" in result
    assert "seniority" in result
    assert "work_model" in result
    assert "Python" in result["extracted_skills"]
    assert "AWS" in result["extracted_skills"]
    assert result["seniority"] == "senior"


def test_enrich_handles_missing_fields():
    """Enrich should work even with minimal job dict."""
    job = {"title": "Engineer"}
    result = enrich(job)
    assert result["extracted_skills"] == []
    assert result["seniority"] is None
    assert result["work_model"] is None


def test_enrich_remote_from_description():
    job = {
        "title": "Developer",
        "description": "This is a remote-first company. Work from home.",
        "tags": [],
    }
    result = enrich(job)
    assert result["work_model"] == "remote"
