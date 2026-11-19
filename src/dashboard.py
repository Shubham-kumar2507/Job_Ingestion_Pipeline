import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .storage import dashboard_data, initialize_database

load_dotenv()


def _default_database_url() -> str:
    return "sqlite:////tmp/job_listings.db" if os.getenv("VERCEL") else "sqlite:///job_listings.db"


DATABASE_URL = os.getenv("DATABASE_URL") or _default_database_url()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="Job ingestion demo")


def _database_backend() -> str:
    return "PostgreSQL" if DATABASE_URL.startswith("postgres") else "SQLite"


@app.get("/", response_class=HTMLResponse)
def home(request: Request, limit: int = 20):
    """Thin proof-of-life dashboard reading from the configured database."""
    initialize_database(DATABASE_URL)
    jobs, last_run, streak, llm_repairs, total_listings = dashboard_data(DATABASE_URL, limit=max(1, min(limit, 100)))
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "jobs": jobs,
            "last_run": last_run,
            "streak": streak,
            "llm_repairs": llm_repairs,
            "total_listings": total_listings,
            "database_backend": _database_backend(),
        },
    )


@app.get("/health", response_class=JSONResponse)
def health():
    """Lightweight health check for monitoring / uptime tools."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
