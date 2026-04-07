"""Fossilrepo MCP Server -- exposes repo operations to AI tools.

Runs as a standalone process communicating over stdio using JSON-RPC 2.0
(Model Context Protocol). Imports Django models directly for DB access.
"""

import json
import os

# Setup Django before any model imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import TextContent  # noqa: E402

from mcp_server.tools import TOOLS, execute_tool  # noqa: E402

server = Server("fossilrepo")


@server.list_tools()
async def list_tools():
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    result = execute_tool(name, arguments)
    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
