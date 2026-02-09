"""Shared fixtures for the test suite."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

import edstem_mcp.server as server_mod


@pytest.fixture(autouse=True)
def _set_token(monkeypatch):
    """Ensure ED_API_TOKEN is always set during tests."""
    monkeypatch.setenv("ED_API_TOKEN", "test-token")


@pytest.fixture()
def mock_client():
    """Return an AsyncMock standing in for EdClient, patched into the server module."""
    client = AsyncMock()
    # Reset the server module's cached client before and after each test
    server_mod._client = None
    with patch.object(server_mod, "_get_client", return_value=client):
        yield client
    server_mod._client = None
