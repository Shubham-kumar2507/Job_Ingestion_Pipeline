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
    → SQLite or PostgreSQL upsert on (source, external_id)
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

The fallback can use Gemini (recommended) or Groq. It is called only when a
record fails deterministic Pydantic validation; valid records do not trigger
LLM calls. Enable it locally with:

```env
LLM_FALLBACK_ENABLED=true
LLM_FALLBACK_PROVIDER=gemini
GEMINI_API_KEY=your_api_key
LLM_FALLBACK_MODEL=gemini-3.5-flash-lite
LLM_FALLBACK_MAX_CALLS=5
```

Groq remains supported with `LLM_FALLBACK_PROVIDER=groq` and `GROQ_API_KEY`.
Calls are capped at 5 per run by default, and output must pass the same
validation before it can be used.

The scheduled job runs in GitHub Actions, not Vercel. Set `GEMINI_API_KEY` as
a repository Actions secret; setting it only in Vercel does not make it
available to the ingestion workflow. The workflow enables fallback, but
ordinary successful feed records still result in zero calls.

### Demonstrate the fallback

In GitHub, choose **Actions → Ingest RemoteOK jobs → Run workflow** and enable
the `demo_llm_fallback` option. This makes one Gemini repair call using a
synthetic invalid record; the fixture is not saved to the listings table.
The schedule never injects the fixture.

For a local demo, set this additional flag in `.env`:

```env
DEMO_INJECT_INVALID_RECORD=true
```

The real RemoteOK payload remains untouched. The fixture has a missing title
and company but a description containing both, forcing one validation failure
and one capped repair attempt. It is excluded from persistence.

## Enrichment

Every validated listing is enriched deterministically (no API calls):

- **Skill extraction**: 30 compiled regex patterns identify technologies (Python, AWS, Docker, etc.)
- **Seniority inference**: Detects intern → junior → mid → senior → staff → principal → lead → manager → director → VP → C-level
- **Work model detection**: Flags remote, hybrid, or onsite from title/description/location

All three fields are stored in the configured database and surfaced on the dashboard.

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

## Deploy to Vercel

The FastAPI dashboard is deployed as a Vercel Python Function. Import this
repository with its root directory set to the repository root; Vercel reads
the app entrypoint and runtime dependencies from `pyproject.toml`.

Set `DATABASE_URL` in the Vercel project to a PostgreSQL connection URL. The
dashboard creates missing tables on its first request. The pipeline is a
separate process and does not run automatically on Vercel; set the same
`DATABASE_URL` locally before running `python -m src.pipeline` to populate
the dashboard.

The app retains SQLite support for local development and tests. Do not commit
database URLs or API keys.

### Automate ingestion

The GitHub Actions workflow at `.github/workflows/ingest.yml` polls the
RemoteOK feed every 15 minutes (best effort; scheduled runs can be delayed).
In the repository, open **Settings → Secrets and variables → Actions** and add
a repository secret named `DATABASE_URL` with the same PostgreSQL connection
URL used by Vercel, plus a `GEMINI_API_KEY` secret for LLM fallback. These
secrets must be added to GitHub separately; Vercel variables are not shared
with Actions. Then open **Actions → Ingest RemoteOK jobs → Run workflow** to
perform a sync. Later runs start on the schedule automatically.

## Design document

See [DESIGN.md](DESIGN.md) for the full write-up covering:
- Detection surface (fingerprinting, timing, headers, behavior)
- Evasion strategy (what's built vs. what's not and why)
- Pipeline resilience (schema changes, rate limits, empty responses)
- LLM fallback cost/latency analysis
- Deterministic enrichment layer (skills, seniority, work model)
- Ethical and technical boundaries on ToS-protected scraping
