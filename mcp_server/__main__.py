"""Entry point for the fossilrepo MCP server.

Usage:
    python -m mcp_server
    fossilrepo-mcp  (via pyproject.toml script entry)
"""

import asyncio

from mcp_server.server import main


def run():
    """Synchronous entry point for pyproject.toml [project.scripts]."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
