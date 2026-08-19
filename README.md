# Job Ingestion MVP

A resilient, compliant job ingestion pipeline that demonstrates anti-detection patterns, structured error handling, an opt-in LLM fallback parser, and deterministic enrichment (skill extraction, seniority inference, remote/hybrid/onsite classification) — all running against RemoteOK's public JSON API.

> This project intentionally does not scrape or automate access to ToS-protected job platforms. See [DESIGN.md](DESIGN.md) for the full ethical and technical rationale.

## Quick start

1. **Create a Python 3.11+ virtual environment and install dependencies:**

   ```powershell
   py -3.11 -m venv .venv  # any Python 3.11+ interpreter
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. **Configure the environment:**

   ```powershell
   Copy-Item .env.example .env
   ```

3. **Run the pipeline** (run twice to verify upsert — the second run updates, not duplicates):

   ```powershell
   python -m src.pipeline
   # or equivalently:
   python -m src
   ```

4. **Run the test suite:**

   ```powershell
   python -m pytest -v
   ```

5. **Start the dashboard** at `http://127.0.0.1:8000`:

   ```powershell
   uvicorn src.dashboard:app --reload
   ```

## Architecture

```
RemoteOK JSON API
    → httpx.AsyncClient + tenacity retry + circuit breaker + UA rotation
    → source parser (field mapping)
    → Pydantic JobListing validation
    → [opt-in] LLM fallback repair on validation failure
    → deterministic enrichment (skills, seniority, work model)
    → SQLite upsert on (source, external_id)
```

**Required normalized fields** (`source`, `external_id`, `title`, `company`, `url`) reject malformed records with a loud validation error. Optional fields (`description`, `location`, `salary_*`, `tags`, etc.) degrade gracefully to `None`, preserving partial records without crashing the pipeline.

## Anti-detection controls

| Control | Implementation |
|---|---|
| UA rotation | 7 realistic browser UA strings, random per request |
| Randomized delay | `uniform(2, 6)` seconds — a fixed interval is itself a fingerprint |
| Realistic headers | `Accept-Language`, `Accept-Encoding`, `Referer` |
| Persistent session | One `httpx.AsyncClient` reused per run |
| Circuit breaker | 3 consecutive failures → exponential cooldown (30 s → 5 min cap) |
| Per-item error boundary | Each record wrapped in try/except; structured JSON error logging |

## LLM fallback

Disabled by default. To enable:

```env
LLM_FALLBACK_ENABLED=true
GROQ_API_KEY=your_rotated_key_here
LLM_FALLBACK_MODEL=openai/gpt-oss-20b
LLM_FALLBACK_MAX_CALLS=5
```

The fallback fires **only** for records that Pydantic rejected. It's capped at 5 calls per run by default, and its output must pass the exact same validation. Logs include invocation count, latency, and token usage so the cost/latency trade-off is visible, not hidden.

### Demonstrate the fallback

For a presentation, set this additional flag in your local `.env`:

```env
DEMO_INJECT_INVALID_RECORD=true
```

The real RemoteOK payload remains untouched. After parsing, the pipeline adds one labelled fixture with a missing title and company but an explicit description containing both. This forces one Pydantic failure and, when the Groq fallback is enabled, one capped LLM repair attempt. Set the flag back to `false` after the demo.

## Enrichment

Every validated listing is enriched deterministically (no API calls):

- **Skill extraction**: 30 compiled regex patterns identify technologies (Python, AWS, Docker, etc.)
- **Seniority inference**: Detects intern → junior → mid → senior → staff → principal → lead → manager → director → VP → C-level
- **Work model detection**: Flags remote, hybrid, or onsite from title/description/location

All three fields are stored in SQLite and surfaced on the dashboard.

## Dashboard

A thin FastAPI + Jinja2 page (premium dark-mode design with glassmorphism) showing:
- Last-run status and timestamp
- Success/failure streak
- Total stored listings count
- Stored/invalid counts and LLM calls used
- **LLM repair audit log** with outcome, repaired fields, and model name
- Latest job listings with **extracted skills**, **seniority badges**, and **work model labels**

Endpoints:
- `GET /` — HTML dashboard
- `GET /health` — JSON health check (`{"status": "ok", "timestamp": "..."}`)

## Design document

See [DESIGN.md](DESIGN.md) for the full write-up covering:
- Detection surface (fingerprinting, timing, headers, behavior)
- Evasion strategy (what's built vs. what's not and why)
- Pipeline resilience (schema changes, rate limits, empty responses)
- LLM fallback cost/latency analysis
- Deterministic enrichment layer (skills, seniority, work model)
- Ethical and technical boundaries on ToS-protected scraping
