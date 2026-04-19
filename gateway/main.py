"""
Sentinel Gateway — FastAPI application entry point.

Start with:  uvicorn gateway.main:app --reload --port 8000
  or:        make run-gateway
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from gateway.auth.api_keys import store_admin_key
from gateway.config import settings
from gateway.models.requests import HealthResponse, ServicesHealth, ServiceStatus
from gateway.routes.agents import router as agents_router
from gateway.routes.decisions import router as decisions_router
from gateway.routes.policies import router as policies_router
from gateway.routes.tool_calls import router as tool_calls_router
from gateway.routes.websocket import router as ws_router
from gateway.websocket.manager import manager as ws_manager
from judge.client import ollama_health
from shared.neo4j_client import close_driver, ping_neo4j
from shared.redis_client import ping_redis

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ----------------------------- Startup -----------------------------------
    log.info("sentinel_gateway_starting", env=settings.gateway_env)

    # Seed admin key into Redis
    from shared.redis_client import rate_limit_client
    redis = rate_limit_client()
    await store_admin_key(redis)
    await redis.aclose()
    log.info("admin_key_seeded")

    # Start WebSocket manager (Redis Pub/Sub listener + heartbeat)
    await ws_manager.startup()
    log.info("ws_manager_started")

    yield

    # ----------------------------- Shutdown ----------------------------------
    log.info("sentinel_gateway_stopping")
    await ws_manager.shutdown()
    await close_driver()
    log.info("sentinel_gateway_stopped")


app = FastAPI(
    title="Sentinel Gateway",
    version="1.0.0",
    description="Real-time AI agent governance layer",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS — allow dashboard dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.gateway_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(tool_calls_router)
app.include_router(decisions_router)
app.include_router(agents_router)
app.include_router(policies_router)
app.include_router(ws_router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, tags=["Observability"])
async def health_check() -> HealthResponse:
    redis_ok = await ping_redis()
    neo4j_ok = await ping_neo4j()
    ollama_ok = await ollama_health()

    services = ServicesHealth(
        redis=ServiceStatus.OK if redis_ok else ServiceStatus.DOWN,
        neo4j=ServiceStatus.OK if neo4j_ok else ServiceStatus.DOWN,
        ollama=ServiceStatus.OK if ollama_ok else ServiceStatus.DOWN,
    )

    overall = "ok" if (redis_ok and neo4j_ok) else "degraded"
    return HealthResponse(status=overall, services=services)


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
@app.get("/metrics", include_in_schema=False, tags=["Observability"])
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {"service": "Sentinel Gateway", "version": "1.0.0", "docs": "/docs"}
