import asyncio
import gzip
import json
import logging
import random
from dataclasses import dataclass, field

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

REMOTEOK_API_URL = "https://remoteok.com/api"

# A larger pool reduces the probability of repeating the same UA on consecutive
# requests, which is itself a detectable pattern in real anti-bot systems.
USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
)
logger = logging.getLogger(__name__)


def log_event(event: str, **fields: object) -> None:
    logger.info(json.dumps({"event": event, **fields}, default=str))


def parse_json_response(response: httpx.Response) -> object:
    """Parse JSON even if an intermediary passes through raw gzip bytes.

    HTTPX normally handles content decoding. RemoteOK's response can arrive as
    raw gzip bytes in some network paths despite the response metadata, so the
    explicit fallback keeps an otherwise healthy ingestion run alive.
    """
    try:
        return response.json()
    except UnicodeDecodeError:
        if not response.content.startswith(b"\x1f\x8b"):
            raise
        log_event("compressed_response_recovered", source="remoteok", encoding="gzip")
        return json.loads(gzip.decompress(response.content).decode("utf-8"))


@dataclass
class CircuitBreaker:
    """Pause a source after repeated request failures during one pipeline run.

    After ``failure_threshold`` consecutive failures the breaker trips. While
    tripped it inserts an exponential cooldown (30 s → 60 s → … → 5 min cap)
    before letting the next request through. A single success resets the count.
    """

    failure_threshold: int = 3
    consecutive_failures: int = 0
    _cooldown_history: list[float] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.consecutive_failures >= self.failure_threshold

    async def before_request(self, source: str) -> None:
        if not self.is_open:
            return
        # 30 s after three failures, then 60 s, 120 s, capped at five minutes.
        pause_seconds = min(30 * (2 ** (self.consecutive_failures - self.failure_threshold)), 300)
        self._cooldown_history.append(pause_seconds)
        log_event(
            "circuit_breaker_open",
            source=source,
            failures=self.consecutive_failures,
            pause_seconds=pause_seconds,
        )
        await asyncio.sleep(pause_seconds)

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self, source: str, error: Exception) -> None:
        self.consecutive_failures += 1
        log_event(
            "source_fetch_failed",
            source=source,
            failures=self.consecutive_failures,
            error=str(error),
        )


@retry(
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    stop=stop_after_attempt(4),
    reraise=True,
)
async def fetch_remoteok_payload(client: httpx.AsyncClient, breaker: CircuitBreaker) -> list[dict]:
    """Fetch RemoteOK's public JSON feed, retrying transient transport/status errors."""
    await breaker.before_request("remoteok")
    # Randomized delay prevents a fixed-interval cadence, which is itself a
    # fingerprint that distinguishes bots from organic traffic.
    delay_seconds = random.uniform(2, 6)
    await asyncio.sleep(delay_seconds)
    user_agent = random.choice(USER_AGENTS)
    log_event("source_request", source="remoteok", delay_seconds=round(delay_seconds, 2), user_agent=user_agent)
    try:
        response = await client.get(
            REMOTEOK_API_URL,
            headers={
                "User-Agent": user_agent,
                "Accept-Language": "en-US,en;q=0.9",
                # Prefer plain JSON. Some network intermediaries pass RemoteOK
                # gzip bytes through without decoding them for HTTPX.
                "Accept-Encoding": "identity",
                "Referer": "https://remoteok.com/",
            },
        )
        response.raise_for_status()
        payload = parse_json_response(response)
        if not isinstance(payload, list):
            raise ValueError("RemoteOK API returned a non-list JSON payload")
        if len(payload) == 0:
            log_event("empty_payload", source="remoteok")
    except (httpx.RequestError, httpx.HTTPStatusError) as error:
        # This sits inside tenacity's retry boundary, so every failed attempt
        # contributes to the breaker and the fourth attempt is cooled down.
        breaker.record_failure("remoteok", error)
        raise
    breaker.record_success()
    return payload


async def fetch_remoteok() -> list[dict]:
    """Use one persistent client for all attempts in this ingestion run.

    Reusing a single ``httpx.AsyncClient`` mirrors the connection-reuse pattern
    of a real browser session and avoids the overhead + fingerprint signal of
    opening a fresh TLS handshake for every request.
    """
    headers = {"Accept": "application/json"}
    timeout = httpx.Timeout(20.0, connect=10.0)
    breaker = CircuitBreaker()
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        return await fetch_remoteok_payload(client, breaker)
