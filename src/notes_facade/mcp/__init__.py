"""MCP package for Notes Facade."""

from notes_facade.mcp.server import mcp
from notes_facade.mcp.tools import register_notes_tools

__all__ = ["mcp", "register_notes_tools"]
