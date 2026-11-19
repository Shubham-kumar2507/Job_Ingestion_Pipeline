"""Tests for the LLM fallback repair path using mocks (no real API calls)."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from src.llm_fallback import LLMFallback


def _make_fallback(enabled=True, max_calls=5):
    """Create a test-ready LLMFallback instance."""
    return LLMFallback(enabled=enabled, model="test-model", max_calls=max_calls)


def test_default_model_is_available_on_current_groq_accounts(monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert LLMFallback.from_environment().model == "openai/gpt-oss-20b"


def test_gemini_key_selects_gemini_provider_and_model(monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    fallback = LLMFallback.from_environment()

    assert fallback.provider == "gemini"
    assert fallback.model == "gemini-3.5-flash-lite"


# ──────────────────────────────────────────────
# Disabled / cap-reached paths
# ──────────────────────────────────────────────

def test_repair_returns_none_when_disabled():
    fb = _make_fallback(enabled=False)
    result = asyncio.run(fb.repair({"title": ""}, "validation error"))
    assert result is None
    assert fb.calls_used == 0


def test_repair_returns_none_when_cap_reached():
    fb = _make_fallback(max_calls=2)
    fb.calls_used = 2  # already at cap
    result = asyncio.run(fb.repair({"title": ""}, "validation error"))
    assert result is None
    assert fb.calls_used == 2


def test_repair_returns_none_without_api_key():
    fb = _make_fallback()
    with patch.dict("os.environ", {"GROQ_API_KEY": ""}, clear=False):
        result = asyncio.run(fb.repair({"title": ""}, "validation error"))
    assert result is None
    assert fb.calls_used == 0


# ──────────────────────────────────────────────
# Successful repair mock
# ──────────────────────────────────────────────

def test_repair_returns_extracted_fields_on_success():
    fb = _make_fallback()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "title": "AI Engineer",
        "company": "Acme Labs",
    })
    mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=20, total_tokens=120)

    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False):
        with patch("src.llm_fallback.Groq") as MockGroq:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            MockGroq.return_value = mock_client

            result = asyncio.run(fb.repair({"title": "", "company": ""}, "title must not be blank"))

    assert result is not None
    assert result["title"] == "AI Engineer"
    assert result["company"] == "Acme Labs"
    assert fb.calls_used == 1


def test_gemini_repair_returns_extracted_fields():
    fb = LLMFallback(enabled=True, model="gemini-test", max_calls=5, provider="gemini")
    response = MagicMock()
    response.output_text = json.dumps({"title": "AI Engineer", "company": "Acme Labs"})
    response.usage = MagicMock(total_input_tokens=100, total_output_tokens=20, total_tokens=120)

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False):
        with patch("src.llm_fallback.genai.Client") as MockGemini:
            mock_client = MagicMock()
            mock_client.interactions.create.return_value = response
            MockGemini.return_value = mock_client

            result = asyncio.run(fb.repair({"title": "", "company": ""}, "title must not be blank"))

    assert result == {"title": "AI Engineer", "company": "Acme Labs"}
    assert fb.calls_used == 1
    MockGemini.assert_called_once_with(api_key="test-key")
    mock_client.interactions.create.assert_called_once()


# ──────────────────────────────────────────────
# API error handling
# ──────────────────────────────────────────────

def test_repair_returns_none_on_api_error():
    fb = _make_fallback()

    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False):
        with patch("src.llm_fallback.Groq") as MockGroq:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("API rate limit")
            MockGroq.return_value = mock_client

            result = asyncio.run(fb.repair({"title": ""}, "error"))

    assert result is None
    assert fb.calls_used == 1  # call was counted even though it failed


# ──────────────────────────────────────────────
# Cap enforcement across multiple calls
# ──────────────────────────────────────────────

def test_repair_respects_cap_across_calls():
    fb = _make_fallback(max_calls=1)
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({"title": "X"})
    mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False):
        with patch("src.llm_fallback.Groq") as MockGroq:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            MockGroq.return_value = mock_client

            # First call succeeds
            result1 = asyncio.run(fb.repair({"title": ""}, "error"))
            assert result1 is not None
            assert fb.calls_used == 1

            # Second call is blocked by cap
            result2 = asyncio.run(fb.repair({"title": ""}, "error"))
            assert result2 is None
            assert fb.calls_used == 1  # not incremented
