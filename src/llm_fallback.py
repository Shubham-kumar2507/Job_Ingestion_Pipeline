"""Opt-in, capped LLM repair for records deterministic validation rejects."""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from google import genai
from groq import Groq

logger = logging.getLogger(__name__)
OUTPUT_FIELDS = "title, company, url, description, location, employment_type, salary_min, salary_max, posted_at"
GEMINI_RESPONSE_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "company": {"type": "string"},
            "url": {"type": "string"},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "employment_type": {"type": "string"},
            "salary_min": {"type": "integer"},
            "salary_max": {"type": "integer"},
            "posted_at": {"type": "string"},
        },
        "required": ["title", "company", "url"],
    },
}


@dataclass
class LLMFallback:
    """A per-run fallback that is a no-op unless explicitly enabled."""

    enabled: bool
    model: str | None
    max_calls: int
    calls_used: int = 0
    provider: str = "groq"

    @classmethod
    def from_environment(cls) -> "LLMFallback":
        provider = os.getenv("LLM_FALLBACK_PROVIDER", "").strip().lower()
        if not provider:
            provider = "gemini" if os.getenv("GEMINI_API_KEY") else "groq"
        if provider not in {"gemini", "groq"}:
            raise ValueError(f"Unsupported LLM_FALLBACK_PROVIDER: {provider}")
        default_model = "gemini-3.5-flash-lite" if provider == "gemini" else "openai/gpt-oss-20b"
        return cls(
            enabled=os.getenv("LLM_FALLBACK_ENABLED", "false").lower() == "true",
            model=os.getenv("LLM_FALLBACK_MODEL") or default_model,
            max_calls=max(0, int(os.getenv("LLM_FALLBACK_MAX_CALLS", "5"))),
            provider=provider,
        )

    @property
    def available(self) -> bool:
        api_key_name = "GEMINI_API_KEY" if self.provider == "gemini" else "GROQ_API_KEY"
        return self.enabled and bool(os.getenv(api_key_name)) and bool(self.model)

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
                    "Omit optional fields not present in the source. Never invent facts."
                ),
            },
            {
                "role": "user",
                "content": f"Pydantic validation error: {validation_error}\nRaw source record:\n{snippet}",
            },
        ]
        started = time.perf_counter()
        try:
            if self.provider == "gemini":
                interaction = genai.Client(api_key=os.environ["GEMINI_API_KEY"]).interactions.create(
                    model=self.model,
                    system_instruction=messages[0]["content"],
                    input=messages[1]["content"],
                    response_format=GEMINI_RESPONSE_FORMAT,
                )
                fields = json.loads(interaction.output_text or "{}")
                usage = getattr(interaction, "usage", None)
                input_tokens = getattr(usage, "total_input_tokens", None)
                output_tokens = getattr(usage, "total_output_tokens", None)
                total_tokens = getattr(usage, "total_tokens", None)
            else:
                client = Groq(api_key=os.environ["GROQ_API_KEY"])
                completion = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0,
                )
                fields = json.loads(completion.choices[0].message.content or "{}")
                usage = completion.usage
                input_tokens = getattr(usage, "prompt_tokens", None)
                output_tokens = getattr(usage, "completion_tokens", None)
                total_tokens = getattr(usage, "total_tokens", None)
        except Exception as error:
            logger.error(json.dumps({
                "event": "llm_fallback_failed", "provider": self.provider, "call": call_number,
                "cap": self.max_calls, "latency_ms": round((time.perf_counter() - started) * 1000),
                "error": str(error),
            }))
            return None

        logger.info(json.dumps({
            "event": "llm_fallback_used", "provider": self.provider, "call": call_number,
            "cap": self.max_calls, "model": self.model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }))
        return fields if isinstance(fields, dict) else None
