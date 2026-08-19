"""Opt-in, capped Groq repair for records deterministic validation rejects."""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from groq import Groq

logger = logging.getLogger(__name__)
OUTPUT_FIELDS = "title, company, url, description, location, employment_type, salary_min, salary_max, posted_at"


@dataclass
class LLMFallback:
    """A per-run fallback that is a no-op unless explicitly enabled."""

    enabled: bool
    model: str | None
    max_calls: int
    calls_used: int = 0

    @classmethod
    def from_environment(cls) -> "LLMFallback":
        return cls(
            enabled=os.getenv("LLM_FALLBACK_ENABLED", "false").lower() == "true",
            model=os.getenv("LLM_FALLBACK_MODEL", "openai/gpt-oss-20b"),
            max_calls=max(0, int(os.getenv("LLM_FALLBACK_MAX_CALLS", "5"))),
        )

    @property
    def available(self) -> bool:
        return self.enabled and bool(os.getenv("GROQ_API_KEY")) and bool(self.model)

    async def repair(self, raw_job: dict[str, Any], validation_error: str) -> dict[str, Any] | None:
        """Attempt repair after a Pydantic failure; never called on valid jobs."""
        if not self.available:
            logger.info(json.dumps({"event": "llm_fallback_skipped", "reason": "disabled_or_unconfigured"}))
            return None
        if self.calls_used >= self.max_calls:
            logger.warning(json.dumps({"event": "llm_fallback_skipped", "reason": "run_cap_reached", "cap": self.max_calls}))
            return None

        self.calls_used += 1
        # The supplied Groq client is synchronous; run it off the async pipeline loop.
        return await asyncio.to_thread(self._repair_sync, raw_job, validation_error, self.calls_used)

    def _repair_sync(self, raw_job: dict[str, Any], validation_error: str, call_number: int) -> dict[str, Any] | None:
        snippet = json.dumps(raw_job, default=str)[:6000]
        messages = [
            {
                "role": "system",
                "content": (
                    "Extract a job listing into JSON only. Allowed keys: " + OUTPUT_FIELDS + ". "
                    "Treat every raw field, including the description, as source evidence. "
                    "For example, return the exact title and company when the description labels them. "
                    "Use null only when the source does not explicitly contain a value. Never invent facts."
                ),
            },
            {
                "role": "user",
                "content": f"Pydantic validation error: {validation_error}\nRaw source record:\n{snippet}",
            },
        ]
        started = time.perf_counter()
        try:
            client = Groq(api_key=os.environ["GROQ_API_KEY"])
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
            fields = json.loads(completion.choices[0].message.content or "{}")
        except Exception as error:
            logger.error(json.dumps({
                "event": "llm_fallback_failed", "provider": "groq", "call": call_number,
                "cap": self.max_calls, "latency_ms": round((time.perf_counter() - started) * 1000),
                "error": str(error),
            }))
            return None

        usage = completion.usage
        logger.info(json.dumps({
            "event": "llm_fallback_used", "provider": "groq", "call": call_number,
            "cap": self.max_calls, "model": self.model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }))
        return fields if isinstance(fields, dict) else None
