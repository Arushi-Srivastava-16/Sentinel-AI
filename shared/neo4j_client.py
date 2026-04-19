"""
Neo4j async driver factory.

Usage:
    from shared.neo4j_client import get_driver, ping_neo4j

    driver = get_driver()
    async with driver.session(database=settings.neo4j_database) as session:
        await session.run("MATCH (n) RETURN count(n)")
"""

from __future__ import annotations

from functools import lru_cache

from neo4j import AsyncDriver, AsyncGraphDatabase

from gateway.config import settings


@lru_cache(maxsize=1)
def get_driver() -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        max_connection_pool_size=50,
        connection_timeout=5.0,
    )


async def ping_neo4j() -> bool:
    """Health check — returns True if Neo4j is reachable."""
    try:
        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as session:
            result = await session.run("RETURN 1 AS ok")
            record = await result.single()
            return record is not None and record["ok"] == 1
    except Exception:
        return False


async def close_driver() -> None:
    """Call on app shutdown to cleanly close connection pool."""
    driver = get_driver()
    await driver.close()
    get_driver.cache_clear()
