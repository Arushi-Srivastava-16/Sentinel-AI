"""
Unit tests for the Ollama circuit breaker.
Uses fakeredis — no real Redis or Ollama required.
"""

import asyncio
import json

import fakeredis.aioredis as fakeredis
import pytest
import pytest_asyncio

from judge.circuit_breaker import CircuitOpenError, CircuitState, OllamaCircuitBreaker


# Patch the rate_limit_client to use fakeredis
_fake_redis_instance = None


@pytest_asyncio.fixture(autouse=True)
async def patch_redis(monkeypatch):
    global _fake_redis_instance
    _fake_redis_instance = fakeredis.FakeRedis(decode_responses=True)

    # Patch both imports of rate_limit_client used by circuit_breaker
    import shared.redis_client as rc
    monkeypatch.setattr(rc, "rate_limit_client", lambda: _fake_redis_instance)

    # Also patch websocket_client to avoid side effects
    monkeypatch.setattr(rc, "websocket_client", lambda: _fake_redis_instance)

    yield
    await _fake_redis_instance.aclose()


def _breaker(fail_max: int = 3, reset_timeout: int = 60) -> OllamaCircuitBreaker:
    return OllamaCircuitBreaker(fail_max=fail_max, reset_timeout=reset_timeout)


class TestInitialState:
    @pytest.mark.asyncio
    async def test_starts_closed(self):
        b = _breaker()
        assert await b.get_state() == CircuitState.CLOSED


class TestFailureAccumulation:
    @pytest.mark.asyncio
    async def test_failures_below_threshold_stay_closed(self):
        b = _breaker(fail_max=3)
        await b.record_failure()
        await b.record_failure()
        assert await b.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failures_at_threshold_open_circuit(self):
        b = _breaker(fail_max=3)
        await b.record_failure()
        await b.record_failure()
        await b.record_failure()
        assert await b.get_state() == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        b = _breaker(fail_max=3)
        await b.record_failure()
        await b.record_failure()
        await b.record_success()   # resets
        await b.record_failure()
        # After reset + 1 failure, should still be closed
        assert await b.get_state() == CircuitState.CLOSED


class TestCircuitOpenBehaviour:
    @pytest.mark.asyncio
    async def test_open_circuit_raises_on_entry(self):
        b = _breaker(fail_max=1)
        await b.record_failure()
        assert await b.get_state() == CircuitState.OPEN

        with pytest.raises(CircuitOpenError):
            async with b:
                pass   # should never reach here

    @pytest.mark.asyncio
    async def test_open_circuit_transitions_to_half_open_after_timeout(self):
        import time
        b = _breaker(fail_max=1, reset_timeout=1)   # 1-second reset
        await b.record_failure()
        assert await b.get_state() == CircuitState.OPEN

        # Manually set opened_at to the past
        state_data = json.dumps({
            "state": "open",
            "opened_at": time.time() - 2,   # 2 seconds ago
            "updated_at": time.time() - 2,
        })
        await _fake_redis_instance.set("sentinel:circuit_breaker:ollama", state_data)

        assert await b.get_state() == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_success_in_half_open_closes_circuit(self):
        import time
        b = _breaker(fail_max=1, reset_timeout=1)
        await b.record_failure()

        # Force to half-open
        state_data = json.dumps({
            "state": "open",
            "opened_at": time.time() - 2,
            "updated_at": time.time() - 2,
        })
        await _fake_redis_instance.set("sentinel:circuit_breaker:ollama", state_data)
        assert await b.get_state() == CircuitState.HALF_OPEN

        # Record success
        await b.record_success()
        assert await b.get_state() == CircuitState.CLOSED


class TestContextManager:
    @pytest.mark.asyncio
    async def test_successful_context_records_success(self):
        b = _breaker(fail_max=5)
        # Add some failures first
        await b.record_failure()
        await b.record_failure()

        async with b:
            pass   # no exception = success

        assert await b.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_exception_in_context_records_failure(self):
        b = _breaker(fail_max=1)

        with pytest.raises(ValueError):
            async with b:
                raise ValueError("Ollama crashed")

        assert await b.get_state() == CircuitState.OPEN
