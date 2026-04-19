"""
Gateway configuration — all values come from environment variables.
Zero hardcoded values anywhere in the codebase.

Usage:
    from gateway.config import settings
    settings.redis_host
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Gateway
    # -------------------------------------------------------------------------
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    gateway_env: str = "development"
    gateway_log_level: str = "INFO"
    gateway_cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    sentinel_admin_key: str = Field(..., description="Root admin API key (snl_admin_...)")

    # -------------------------------------------------------------------------
    # JWT
    # -------------------------------------------------------------------------
    jwt_private_key_path: str = "./certs/private.pem"
    jwt_public_key_path: str = "./certs/public.pem"
    jwt_algorithm: str = "RS256"
    jwt_ttl_minutes: int = 15

    # -------------------------------------------------------------------------
    # Redis
    # -------------------------------------------------------------------------
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""

    redis_db_rate_limit: int = 0
    redis_db_audit_stream: int = 1
    redis_db_websocket: int = 2
    redis_db_judge_cache: int = 3

    rate_limit_tokens_per_minute: int = 50
    rate_limit_bucket_size: int = 50

    audit_stream_name: str = "sentinel:audit:events"
    audit_consumer_group: str = "neo4j-writers"
    audit_stream_maxlen: int = 100_000
    audit_dlq_stream: str = "sentinel:audit:dlq"

    ws_pubsub_channel: str = "sentinel:dashboard:events"

    judge_cache_ttl_seconds: int = 300

    # -------------------------------------------------------------------------
    # Neo4j
    # -------------------------------------------------------------------------
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = Field(..., description="Neo4j password")
    neo4j_database: str = "sentinel"

    # -------------------------------------------------------------------------
    # Ollama / Judge Tier 1
    # -------------------------------------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_timeout_seconds: int = 3
    ollama_keep_alive: str = "5m"

    circuit_breaker_fail_max: int = 5
    circuit_breaker_reset_timeout_seconds: int = 60

    # -------------------------------------------------------------------------
    # OpenAI / Judge Tier 3
    # -------------------------------------------------------------------------
    openai_api_key: str = Field(default="", description="OpenAI API key for Judge Tier 3")
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: int = 20
    openai_base_url: str = "https://api.openai.com/v1"

    # Force routing controls (useful for security hardening and demos)
    force_cognitive_path: bool = False
    judge_force_tier3_openai: bool = False

    judge_tier1_confidence_threshold: float = 0.75
    judge_cognitive_path_budget_seconds: float = 20.0

    # -------------------------------------------------------------------------
    # Langfuse
    # -------------------------------------------------------------------------
    langfuse_host: str = "http://localhost:3001"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # -------------------------------------------------------------------------
    # Prometheus
    # -------------------------------------------------------------------------
    prometheus_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return self.gateway_env == "production"

    @property
    def redis_url_rate_limit(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db_rate_limit}"

    @property
    def redis_url_audit_stream(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db_audit_stream}"

    @property
    def redis_url_websocket(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db_websocket}"

    @property
    def redis_url_judge_cache(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db_judge_cache}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Convenience singleton
settings = get_settings()
