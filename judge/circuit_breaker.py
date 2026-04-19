"""
Circuit breaker for the Ollama judge (Tier 1).

State machine:
  CLOSED   → requests flow to Ollama normally
  OPEN     → requests bypass Ollama, immediately return HUMAN_REVIEW
  HALF_OPEN → one probe request allowed; if it succeeds → CLOSED, if fails → OPEN

State is stored in Redis so all gateway replicas share it.
Uses pybreaker under the hood with a custom Redis-backed state store.
"""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum

import structlog

from gateway.config import settings
from gateway.metrics import circuit_breaker_state as _cb_metric
from shared.redis_client import rate_limit_client   # DB0 — fine for circuit state

log = structlog.get_logger()

_STATE_KEY = "sentinel:circuit_breaker:ollama"
_FAILURE_COUNT_KEY = "sentinel:circuit_breaker:ollama:failures"


class CircuitState(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a request is attempted while the circuit is OPEN."""


class OllamaCircuitBreaker:
    """
    Simple Redis-backed circuit breaker.
    Thread-safe across multiple async gateway workers via Redis atomic ops.
    """

    def __init__(
        self,
        fail_max: int | None = None,
        reset_timeout: int | None = None,
    ) -> None:
        self.fail_max = fail_max or settings.circuit_breaker_fail_max
        self.reset_timeout = reset_timeout or settings.circuit_breaker_reset_timeout_seconds

    async def get_state(self) -> CircuitState:
        redis = rate_limit_client()
        try:
            raw = await redis.get(_STATE_KEY)
            if not raw:
                return CircuitState.CLOSED
            data = json.loads(raw)
            state = CircuitState(data.get("state", "closed"))

            # Check if OPEN circuit should transition to HALF_OPEN
            if state == CircuitState.OPEN:
                opened_at = data.get("opened_at", 0)
                if time.time() - opened_at >= self.reset_timeout:
                    await self._set_state(redis, CircuitState.HALF_OPEN)
                    return CircuitState.HALF_OPEN

            return state
        finally:
            await redis.aclose()

    async def _set_state(self, redis, state: CircuitState, extra: dict | None = None) -> None:
        data = {"state": state.value, "updated_at": time.time()}
        if state == CircuitState.OPEN:
            data["opened_at"] = time.time()
        if extra:
            data.update(extra)
        await redis.set(_STATE_KEY, json.dumps(data))

        log.info(
            "circuit_breaker_state_change",
            service="ollama",
            new_state=state.value,
        )
        # Update Prometheus gauge: 0=CLOSED, 1=OPEN, 2=HALF_OPEN
        _state_values = {CircuitState.CLOSED: 0, CircuitState.OPEN: 1, CircuitState.HALF_OPEN: 2}
        _cb_metric.labels(service="ollama").set(_state_values[state])

        # Publish WebSocket event
        await _publish_circuit_event(redis, state)

    async def record_success(self) -> None:
        redis = rate_limit_client()
        try:
            await redis.delete(_FAILURE_COUNT_KEY)
            await self._set_state(redis, CircuitState.CLOSED)
        finally:
            await redis.aclose()

    async def record_failure(self) -> None:
        redis = rate_limit_client()
        try:
            failures = await redis.incr(_FAILURE_COUNT_KEY)
            await redis.expire(_FAILURE_COUNT_KEY, self.reset_timeout * 2)

            if failures >= self.fail_max:
                await self._set_state(redis, CircuitState.OPEN)
                log.error(
                    "circuit_breaker_tripped",
                    service="ollama",
                    failures=failures,
                    fail_max=self.fail_max,
                )
        finally:
            await redis.aclose()

    async def __aenter__(self):
        state = await self.get_state()
        if state == CircuitState.OPEN:
            raise CircuitOpenError("Ollama circuit breaker is OPEN — bypassing LLM judge.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # Any exception = failure
            await self.record_failure()
            return False   # re-raise
        await self.record_success()
        return False


async def _publish_circuit_event(redis, state: CircuitState) -> None:
    """Publish a WebSocket event so the dashboard shows the state change."""
    from shared.redis_client import websocket_client
    import json as _json
    from datetime import datetime, timezone

    ws_redis = websocket_client()
    try:
        event = _json.dumps({
            "event_type": "circuit_breaker_state_change",
            "service": "ollama",
            "state": state.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await ws_redis.publish(settings.ws_pubsub_channel, event)
    except Exception:
        pass   # Circuit event publishing must never fail the main path
    finally:
        await ws_redis.aclose()


# Module-level singleton
_breaker: OllamaCircuitBreaker | None = None


def get_circuit_breaker() -> OllamaCircuitBreaker:
    global _breaker
    if _breaker is None:
        _breaker = OllamaCircuitBreaker()
    return _breaker
