"""Entry point: ``python -m src`` runs the ingestion pipeline."""

import asyncio

from .pipeline import run

asyncio.run(run())
