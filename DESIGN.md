# Design: Resilient, Compliant Job Ingestion Pipeline

## 0. Scope & source commitment

**Live source:** RemoteOK's public JSON API (`https://remoteok.com/api`) — no authentication, no ToS friction, structured JSON data.

**Differentiators:**

1. **LLM fallback parser (Phase 3):** When deterministic Pydantic validation rejects a record, the pipeline optionally invokes Groq's `openai/gpt-oss-20b` model to extract the same normalized schema from the raw data. This is the direct, demonstrable answer to "what keeps the pipeline running when markup changes?"
2. **Deterministic enrichment layer:** Every validated listing is enriched with extracted skills, inferred seniority level, and work model classification (remote/hybrid/onsite) — all deterministic, zero-cost regex patterns. This is the optional stretch goal realized.
3. **Thin proof-of-life dashboard (Phase 4):** A FastAPI + Jinja2 page that reads from the same SQLite database used by the pipeline, showing last-run status, streak, stored/invalid counts, LLM repair audit log, enrichment data, and the newest listings.

Everything past the core pipeline is time-permitting.

---

## 1. What gives an automated client away

Anti-bot systems identify scrapers through a combination of signals. Any single signal is often insufficient, but the *intersection* of several weak signals creates a strong classifier.

### 1.1 Fingerprinting

| Surface | What's checked | How bots fail |
|---|---|---|
| **User-Agent** | String format, version plausibility, consistency across requests | Static or empty UA; UAs from outdated browser versions; UA that doesn't match the TLS fingerprint (JA3/JA4) |
| **TLS fingerprint (JA3/JA4)** | Cipher suites, extensions, curve ordering in the TLS ClientHello | Python's `ssl`/`httpx` produces a JA3 hash that is trivially distinguishable from Chrome/Firefox. Headless browsers often share the same JA3 across all instances |
| **HTTP/2 fingerprint (Akamai)** | SETTINGS frame parameters, WINDOW_UPDATE, PRIORITY frames | `httpx` (h2) and `requests` use library defaults that differ from real browsers |
| **JavaScript environment** | `navigator.webdriver`, `navigator.plugins`, canvas/WebGL hashes, font enumeration | Headless browsers expose `webdriver=true` unless patched; missing plugin lists; uniform canvas hashes across instances |
| **TCP/IP stack** | TTL, window size, MSS, TCP options ordering | Cloud VPS IP ranges produce OS-level fingerprints that don't match claimed User-Agent OS |

### 1.2 Timing & cadence

| Pattern | Detection method |
|---|---|
| **Fixed intervals** | Coefficient of variation ≈ 0 across request timestamps; real humans have high jitter |
| **Sub-second pacing** | No human reads a page in 200 ms and clicks the next link |
| **Burst-then-idle** | Cron jobs produce a predictable sawtooth pattern; real traffic follows diurnal curves |
| **Zero dwell time** | Requesting a page and never loading its assets (CSS, images, fonts) |

### 1.3 Headers & protocol

| Signal | Explanation |
|---|---|
| **Missing or wrong `Accept-*` headers** | Real browsers send `Accept-Language`, `Accept-Encoding`, `Sec-Fetch-*` headers; raw HTTP clients usually don't |
| **Referrer absence** | Direct API hits without a prior page navigation lack the `Referer` header |
| **Cookie behavior** | Not sending or not persisting cookies from prior responses signals a stateless scraper |
| **HTTP version mismatch** | Claiming to be Chrome 125 but connecting via HTTP/1.1 |

### 1.4 Behavioral patterns

| Behavior | Why it's suspicious |
|---|---|
| **Linear URL traversal** | Visiting `/jobs/1`, `/jobs/2`, `/jobs/3` in order rather than the shuffled discovery path of a real user |
| **No asset loading** | A real browser fetches CSS, JS, images; a scraper fetches only the HTML |
| **No mouse/keyboard events** | JS-heavy sites track `mousemove`, `scroll`, `keydown` to build an interaction profile |
| **High page-to-unique-page ratio** | Requesting the same search results page repeatedly with no variation |

---

## 2. Evasion strategy (what this demo builds vs. what production would add)

### 2.1 What this demo implements

These are conservative traffic-hygiene controls appropriate for a permitted public-API source:

| Control | Implementation | Rationale |
|---|---|---|
| **User-Agent rotation** | Pool of 7 realistic, currently-plausible browser UA strings; random selection per request | Prevents the trivial "single static UA" signal |
| **Randomized delay** | `random.uniform(2, 6)` seconds before each request | A fixed interval is itself a fingerprint; uniform random jitter is the simplest effective mitigation |
| **Realistic headers** | `Accept-Language`, `Accept-Encoding`, `Referer` added to requests | Reduces the "bare HTTP client" fingerprint |
| **Persistent session** | One `httpx.AsyncClient` reused for the entire run | Mirrors browser connection-reuse behavior; avoids the overhead and signal of a new TLS handshake per request |
| **Circuit breaker** | After 3 consecutive failures: exponential cooldown (30 s → 60 s → ... → 5 min cap) | Stops hammering a source that's clearly blocking you |
| **Per-item error boundary** | Each record's fetch→parse→validate wrapped in try/except; structured JSON error log | A single malformed record doesn't crash the batch |
| **Retry with backoff** | `tenacity` exponential backoff (1 s → 2 s → 4 s → max 20 s, 4 attempts) | Handles transient network/HTTP errors without aggressive retry storms |

### 2.2 What this demo deliberately does NOT build

The demo source is a public JSON API. The following techniques are discussed for completeness but are **not implemented** because they would be unnecessary for the demo source and, when misapplied, would be evasion of access controls rather than traffic hygiene:

| Technique | When it's needed | Why it's excluded |
|---|---|---|
| **Playwright / headless browser** | JS-rendered pages where the job data is loaded client-side | RemoteOK serves structured JSON; no JS execution needed |
| **Browser fingerprint spoofing** | Sites using canvas/WebGL/font fingerprinting to detect automation | The demo API doesn't serve HTML, let alone run fingerprint JS |
| **Proxy rotation** | High-volume scraping where a single IP's request rate exceeds the target's threshold | A single pipeline run makes one request; proxy rotation would be overengineering |
| **CAPTCHA solving** | Sites that gate content behind CAPTCHAs | No CAPTCHAs on the public API |
| **Account/session simulation** | Sites requiring login to view listings | RemoteOK requires no authentication |
| **Residential proxy pools** | Datacenter IP detection by the target | One request per run from a known development machine |

### 2.3 How a production system would handle a JS-heavy target (e.g., LinkedIn)

If the project were extended to a JS-heavy, access-controlled site (with proper authorization):

1. **Browser automation via Playwright + stealth plugins**: Patch `navigator.webdriver`, randomize canvas/WebGL fingerprints, inject realistic plugin lists.
2. **Residential proxy rotation**: Rotate through a pool of residential IPs (e.g., BrightData, Oxylabs) to avoid datacenter IP blacklisting. Geographic distribution should match the claimed `Accept-Language`.
3. **Session management**: Maintain warm cookie jars, login sessions, and realistic navigation paths (landing page → search → paginate → detail page).
4. **TLS fingerprint matching**: Use libraries like `curl_cffi` or `tls-client` to match Chrome's JA3 fingerprint rather than Python's default.
5. **Request interleaving**: Mix target requests with benign traffic to dilute the scraping signal.
6. **Human-like interaction simulation**: Random scroll events, variable dwell time per page, occasional "dead-end" navigations.

---

## 3. Ingestion strategy & resilience

### 3.1 Pipeline architecture

```
RemoteOK JSON API
       │
       ▼
┌──────────────────┐    tenacity retry (exp. backoff, 4 attempts)
│  httpx.AsyncClient│◄── circuit breaker (3 failures → cooldown)
│  + UA rotation    │    random delay 2–6 s
└────────┬─────────┘
         │ list[dict]
         ▼
┌──────────────────┐
│  Source Parser    │    maps source-specific fields → normalized dicts
│  (parse_remoteok)│    skips metadata records, coerces types
└────────┬─────────┘
         │ list[dict]
         ▼
┌──────────────────┐
│  Pydantic        │    required: source, external_id, title, company, url
│  JobListing      │    optional: description, location, salary, tags, etc.
│  validation      │──── fail? ──► LLM fallback repair (opt-in, capped)
└────────┬─────────┘              │
         │ valid JobListing       │ re-validate repaired dict
         ▼                        ▼
┌──────────────────┐
│  SQLite upsert   │    ON CONFLICT (source, external_id) → UPDATE
│  via SQLAlchemy  │    repeat runs update, never duplicate
└──────────────────┘
         │
         ▼
┌──────────────────┐
│  IngestionRunRow │    tracks: status, counts, timestamps, LLM calls
└──────────────────┘
```

### 3.2 How it survives markup/schema changes

| Scenario | Defense |
|---|---|
| **Source adds/removes fields** | `ConfigDict(extra="ignore")` on the Pydantic model + optional fields with `None` defaults; new source fields are silently ignored; removed optional fields degrade gracefully |
| **Source renames a field** | The parser layer (`parse_remoteok`) localizes the mapping; a rename breaks only the parser function, not the model or storage layers. Structured error logs pinpoint which field failed |
| **Source changes data types** | Pydantic's strict validation catches type mismatches; the parser pre-coerces known edge cases (e.g., string salary → `None`) |
| **LLM fallback (opt-in)** | When validation fails, the raw record is sent to an LLM with the validation error; the LLM attempts to extract the normalized fields; the result is re-validated through the same Pydantic model |

### 3.3 How it survives rate-limits & empty responses

| Scenario | Defense |
|---|---|
| **HTTP 429 (Too Many Requests)** | `tenacity` retries with exponential backoff; the circuit breaker adds increasing cooldown after consecutive failures |
| **HTTP 5xx** | Same retry + circuit breaker path |
| **Empty JSON array** | Logged as `empty_payload` event; the pipeline proceeds with zero records; the ingestion run is recorded as `success` with `stored_count=0` |
| **Network timeout** | `httpx.Timeout(20s, connect=10s)` + `tenacity` retry on `RequestError` |
| **Source completely down** | After 4 retry attempts (tenacity) × 3 consecutive failures (circuit breaker) with escalating cooldowns, the run fails and is recorded with the error message |

---

## 4. LLM fallback parser

### 4.1 When it triggers

The Groq LLM fallback is **not** a per-listing enrichment path. It is:
- **Disabled by default** (`LLM_FALLBACK_ENABLED=false`)
- Triggered **only after** Pydantic `ValidationError` on a specific record
- **Capped per run** at `LLM_FALLBACK_MAX_CALLS` (default: 5)
- Applied to the **minimum useful context**: one truncated raw record + the validation error message

The fallback uses Groq JSON mode. The prompt restricts the allowed normalized field names, and the returned object is still re-validated by Pydantic before it can be stored. `GROQ_API_KEY` is environment-only and is never committed.

### 4.2 Cost/latency trade-off

| Dimension | Control |
|---|---|
| **Cost** | Per-run cap prevents runaway spend; default cap of 5 calls bounds worst-case cost to ~5 × (input tokens + output tokens) × model rate |
| **Latency** | Each LLM call adds 500 ms–3 s; since it only fires on failures (expected: <1% of records), the pipeline-level impact is negligible |
| **Observability** | Every call logs: invocation number, cap, model name, latency (ms), input/output/total token counts |
| **Safety** | The source and external_id remain deterministic (from the parser); LLM output only fills missing enrichment fields; the repaired record must pass the exact same Pydantic validation again; an invalid LLM response is logged and discarded |

### 4.3 What it demonstrates

This directly addresses the role's requirement:
> "What keeps the pipeline running when markup changes?"

Instead of a theoretical answer, this is a **real, demonstrable mechanism**: the pipeline's deterministic parser fails → the LLM extracts the same schema from the raw data → the result is re-validated through the same strict model. It's a concrete answer to an AI-team evaluator.

### Demo fixture

`DEMO_INJECT_INVALID_RECORD=true` is a presentation-only toggle. It appends one clearly labelled, intentionally incomplete normalized record after the real RemoteOK response has been parsed; the API response itself is never altered. The fixture fails deterministic validation because title and company are blank but includes those values in its raw description, visibly exercising exactly one Groq fallback attempt. The toggle defaults to `false` and should remain off in normal runs.

---

## 5. Ethical & technical boundary

### 5.1 Where the line is

| Principle | Position |
|---|---|
| **Access controls are boundaries, not obstacles** | If a site requires login, CAPTCHAs, or explicit `robots.txt` denial, those are deliberate access controls. Bypassing them is not "scraping" — it's unauthorized access |
| **ToS compliance** | The live demo uses only RemoteOK's public, unauthenticated JSON API. A production integration with a ToS-protected site (LinkedIn, Indeed, Naukri) requires either an official API, a partner data feed, or explicit written permission |
| **Rate respect** | Even against a permissive API, the pipeline paces requests (2–6 s random delay) and implements backoff. Good citizenship is not optional |
| **No credential stuffing** | The pipeline never logs into any service, creates accounts, or simulates user sessions |
| **Data handling** | Scraped data is stored locally in SQLite. No PII processing beyond what the source API publicly exposes |

### 5.2 The realistic production path

For a real product that needs job data from LinkedIn/Indeed:
1. **Official APIs first**: LinkedIn Partner API, Indeed Publisher API, etc.
2. **Data provider partnerships**: Companies like Coresignal, Proxycurl, or People Data Labs aggregate and license job data legally.
3. **RSS/Atom feeds**: Many job boards provide syndication feeds.
4. **Direct scraping as last resort**: Only with legal review, explicit ToS compliance, and at rates that don't impact the target's infrastructure.

---

## 6. Demo surface

A thin FastAPI + Jinja2 page at `/` reads the same SQLite database used by the pipeline. It displays:

- **Last-run status** (success/failed) with timestamp
- **Current streak** (consecutive same-status runs)
- **Stored/invalid counts** from the last run
- **LLM fallback calls used** in the last run
- **Latest job listings** with role, company, location, tags, and posted date
- **`/health`** endpoint returning `{"status": "ok", "timestamp": "..."}` for programmatic monitoring

This is intentionally a proof-of-life surface, not a second product. It neither fetches sources nor changes ingestion behavior.

---

## 7. Deterministic enrichment layer

Every listing that passes Pydantic validation (or LLM repair) runs through a zero-cost, deterministic enrichment pass before storage:

| Enrichment | Method | Output |
|---|---|---|
| **Skill extraction** | 30 compiled regex patterns matched against title + description + tags | `extracted_skills: list[str]` — e.g. `["Python", "AWS", "Docker"]` |
| **Seniority inference** | Pattern-matched against title (intern → c-level) | `seniority: str \| None` — e.g. `"senior"`, `"lead"` |
| **Work model detection** | Keywords for remote/hybrid/onsite in title + description + location | `work_model: str \| None` — e.g. `"remote"` |

### Why this matters

The spec's optional stretch goal asks to "normalize seniority, extract a skills list, flag remote/hybrid." This enrichment layer delivers all three without any LLM cost. It runs on every listing (not just failures), adding genuine analytical value to the stored data.

The enrichment is:
- **Deterministic**: Same input always produces the same output; no API calls, no latency variance
- **Testable**: Fully unit-tested with explicit pattern expectations
- **Extensible**: Adding a new skill or seniority level is a one-line regex addition
- **Visible**: The dashboard surfaces extracted skills, seniority badges, and work model labels directly in the listings table

---

## 8. Project structure

```
├── .env.example          # Environment config template
├── .gitignore
├── DESIGN.md             # This document
├── README.md             # Quick-start guide
├── requirements.txt      # Pinned dependency ranges
├── src/
│   ├── __init__.py
│   ├── __main__.py       # Entry point: python -m src
│   ├── dashboard.py      # FastAPI app: / and /health routes
│   ├── enrichment.py     # Deterministic skill/seniority/work-model extraction
│   ├── fetch.py          # httpx client, UA rotation, circuit breaker, retry
│   ├── llm_fallback.py   # Opt-in LLM repair for validation failures
│   ├── models.py         # Pydantic JobListing model
│   ├── parsers.py        # Source-specific field mapping (RemoteOK)
│   ├── pipeline.py       # Orchestration: fetch → parse → validate → enrich → store
│   ├── storage.py        # SQLAlchemy models, SQLite upsert, run tracking
│   └── templates/
│       └── index.html    # Dashboard Jinja2 template (dark-mode glassmorphism)
└── tests/
    ├── __init__.py
    ├── test_enrichment.py # Skill extraction, seniority, work model tests
    ├── test_llm_fallback.py # Mock-based LLM fallback repair tests
    ├── test_parsers.py   # Parser, model, and circuit breaker tests
    ├── test_pipeline.py  # Pipeline fixture tests
    └── test_storage.py   # SQLite upsert tests
```
