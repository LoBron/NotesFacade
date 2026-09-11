"""Runtime acceptance check executed inside the facade container."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping
from typing import Any

import httpx
from fastmcp import Client

EXPECTED_TOOLS = {
    "capture",
    "find",
    "read_note",
    "patch",
    "move_note",
    "review_queue",
    "validate",
}


def _validation_report(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        if isinstance(value.get("errors"), list) and isinstance(value.get("counters"), Mapping):
            return value
        for nested in value.values():
            report = _validation_report(nested)
            if report is not None:
                return report
    if isinstance(value, list):
        for nested in value:
            report = _validation_report(nested)
            if report is not None:
                return report
    return None


async def check_runtime(project_id: str) -> None:
    api_url = os.environ["OBSIDIAN_API_URL"].rstrip("/")
    api_key = os.environ["OBSIDIAN_API_KEY"]
    timeout = httpx.Timeout(20.0, connect=5.0)

    async with httpx.AsyncClient(timeout=timeout) as http_client:
        response = await http_client.get(
            f"{api_url}/vault/",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()

    async with Client("http://127.0.0.1:8000/mcp") as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}
        if tool_names != EXPECTED_TOOLS:
            missing = sorted(EXPECTED_TOOLS - tool_names)
            unexpected = sorted(tool_names - EXPECTED_TOOLS)
            raise RuntimeError(f"MCP tools mismatch; missing={missing}, unexpected={unexpected}")

        result = await client.call_tool("validate", {"project_id": project_id})
        if result.is_error:
            raise RuntimeError("MCP validate returned an error result")
        report = _validation_report(result.structured_content)
        if report is None:
            for content in result.content:
                text = getattr(content, "text", None)
                if text is None:
                    continue
                try:
                    report = _validation_report(json.loads(text))
                except json.JSONDecodeError:
                    continue
                if report is not None:
                    break
        if report is None:
            raise RuntimeError("MCP validate response has no validation report")
        if report["errors"]:
            raise RuntimeError(f"MCP validate found {len(report['errors'])} error(s)")

    print("REST authenticated: ok")
    print("MCP initialize/tools/list: ok (7 tools)")
    print("MCP validate: ok (0 errors)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    arguments = parser.parse_args()
    asyncio.run(check_runtime(arguments.project_id))


if __name__ == "__main__":
    main()
