"""
Shared pytest configuration and fixtures.
"""

import os

import pytest

# Set required env vars before any imports that trigger settings loading
os.environ.setdefault("SENTINEL_ADMIN_KEY", "snl_admin_test_key")
os.environ.setdefault("NEO4J_PASSWORD", "test_password")
os.environ.setdefault("GATEWAY_ENV", "development")


def pytest_configure(config):
    """Register asyncio mode."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
