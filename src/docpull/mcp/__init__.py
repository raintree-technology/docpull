"""MCP (Model Context Protocol) server for docpull.

Exposes ``docpull`` as a tool for AI agents: fetch docs on demand, list
sources, grep through fetched content. Runs as ``docpull mcp`` via stdio.
"""

from .server import run_mcp_server

__all__ = ["run_mcp_server"]
