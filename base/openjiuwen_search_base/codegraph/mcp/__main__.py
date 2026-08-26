"""Run the Jiuwen Code Parser MCP server: ``python -m openjiuwen_search_base.codegraph.mcp``."""

import os

# Keep MCP stdio JSON-RPC clean: tqdm must not write progress to stdout.
os.environ.setdefault("TQDM_DISABLE", "1")

from .server import JiuwenMCP

if __name__ == "__main__":
    JiuwenMCP().mcp.run(show_banner=False)
