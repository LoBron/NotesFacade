from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from notes_facade.mcp.tools import register_notes_tools
from notes_facade.projects.errors import UNKNOWN_PROJECT_ERROR_MESSAGE, UnknownProjectError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "method_name"),
    [
        (
            "capture",
            {"project_id": "missing", "title": "A", "text": "B", "type": "thought"},
            "capture",
        ),
        ("find", {"project_id": "missing", "query": "x"}, "find"),
        ("read_note", {"project_id": "missing", "path": "10 Notes/a.md"}, "read_note"),
        (
            "patch",
            {
                "project_id": "missing",
                "path": "10 Notes/a.md",
                "op": {"kind": "set_field", "field": "status", "value": "active"},
            },
            "patch",
        ),
        (
            "move_note",
            {"project_id": "missing", "path": "10 Notes/a.md", "new_path": "90 Archive/a.md"},
            "move_note",
        ),
        ("review_queue", {"project_id": "missing"}, "review_queue"),
        ("validate", {"project_id": "missing"}, "validate"),
    ],
)
async def test_tools_map_unknown_project_error_to_unified_message(
    tool_name: str, arguments: dict[str, object], method_name: str
):
    service = type("ServiceStub", (), {})()
    for attr in (
        "capture",
        "find",
        "read_note",
        "patch",
        "move_note",
        "review_queue",
        "validate",
    ):
        setattr(service, attr, AsyncMock(return_value=None))
    getattr(service, method_name).side_effect = UnknownProjectError()

    mcp = FastMCP("test")
    register_notes_tools(service=service, mcp_server=mcp)

    with pytest.raises(ToolError, match=UNKNOWN_PROJECT_ERROR_MESSAGE):
        await mcp.call_tool(tool_name, arguments)
