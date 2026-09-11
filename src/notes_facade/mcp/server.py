"""FastMCP server instance for Notes Facade."""

from fastmcp import FastMCP

MCP_SERVER_NAME = "Notes Facade"
MCP_SERVER_INSTRUCTIONS = (
    "MCP facade для безопасной работы с заметками Obsidian по непрозрачному project_id."
)

mcp = FastMCP(name=MCP_SERVER_NAME, instructions=MCP_SERVER_INSTRUCTIONS)
