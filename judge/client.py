"""
Ollama HTTP client for Judge Tier 1 (local Llama inference).

Uses httpx for async requests and instructor for structured output.
"""

from __future__ import annotations

from typing import Any, TypeVar

import httpx
import instructor
from openai import AsyncOpenAI

from gateway.config import settings

T = TypeVar("T")


def _get_ollama_instructor_client() -> instructor.AsyncInstructor:
    """
    Create an instructor-patched AsyncOpenAI client pointed at Ollama.
    Ollama exposes an OpenAI-compatible API at /v1.
    """
    raw = AsyncOpenAI(
        base_url=f"{settings.ollama_base_url}/v1",
        api_key="ollama",   # Ollama doesn't check the key
        timeout=settings.ollama_timeout_seconds,
    )
    return instructor.from_openai(raw, mode=instructor.Mode.JSON)


async def ollama_chat(
    prompt: str,
    response_model: type[T],
    max_retries: int = 2,
) -> T:
    """
    Send a prompt to Ollama and parse the response into `response_model`.
    Uses instructor's automatic retry on parse failure.

    Raises:
        httpx.TimeoutException: if Ollama doesn't respond within timeout
        instructor.exceptions.InstructorRetryException: if output can't be parsed
    """
    client = _get_ollama_instructor_client()
    result = await client.chat.completions.create(
        model=settings.ollama_model,
        messages=[{"role": "user", "content": prompt}],
        response_model=response_model,
        max_retries=max_retries,
    )
    return result


async def ollama_health() -> bool:
    """Check if Ollama is reachable and has the model loaded."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code != 200:
                return False
            tags = resp.json().get("models", [])
            model_name = settings.ollama_model.split(":")[0]
            return any(model_name in t.get("name", "") for t in tags)
    except Exception:
        return False
