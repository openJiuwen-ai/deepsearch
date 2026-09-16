"""MCP server tests."""

from importlib.metadata import version

import pytest
import rich

rich_ver = version("rich")
if not hasattr(rich, "__version__"):
    rich.__version__ = rich_ver
pytest.importorskip(
    "rich", minversion="13.9.4", reason=f"Impossible CI env: rich=={rich_ver}, possibly namespace pollution somewhere!"
)
pytest.importorskip("fastmcp", minversion="2")
