"""MCP server tests."""

import pytest

pytest.importorskip(
    "rich", minversion="13.9.4", reason="Impossible CI env resolution: rich<13.9.4, contradicting fastmcp requirement!"
)
pytest.importorskip("fastmcp", minversion="2")
