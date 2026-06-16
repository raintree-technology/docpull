"""MCP (Model Context Protocol) server for docpull.

Exposes ``docpull`` as a tool for AI agents: fetch web sources on demand,
list source aliases, and grep through fetched Markdown. Runs as
``docpull mcp`` via stdio.
"""

from .server import run_mcp_server

__all__ = ["run_mcp_server"]
