"""Health endpoint registration for the MCP ASGI app."""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from notes_facade.obsidian.client import ObsidianRestApiHttpClient
from notes_facade.obsidian.errors import ObsidianClientError
from notes_facade.obsidian.transport import ObsidianHttpTransportClient
from notes_facade.projects.registry import ProjectRegistry
from notes_facade.settings import Settings

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
OBSIDIAN_DEGRADED_REASON = "obsidian api is unavailable"
PROJECTS_CONFIG_DEGRADED_REASON = "projects config is invalid"


def register_health_route(*, mcp_server: FastMCP, settings: Settings) -> None:
    """Register `/health` route for config and Obsidian availability checks."""

    @mcp_server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        reasons: list[str] = []
        if not _is_projects_config_valid(settings=settings):
            reasons.append(PROJECTS_CONFIG_DEGRADED_REASON)
        if not await _is_obsidian_api_available(settings=settings):
            reasons.append(OBSIDIAN_DEGRADED_REASON)

        status = STATUS_OK if not reasons else STATUS_DEGRADED
        return JSONResponse({"status": status, "reasons": reasons})


def _is_projects_config_valid(*, settings: Settings) -> bool:
    try:
        ProjectRegistry(settings.projects_config_path)
    except ValueError:
        return False
    return True


async def _is_obsidian_api_available(*, settings: Settings) -> bool:
    transport = ObsidianHttpTransportClient(
        base_url=settings.obsidian_api_url,
        bearer_token=settings.obsidian_api_key,
        connect_timeout_seconds=settings.obsidian_connect_timeout_seconds,
        read_timeout_seconds=settings.obsidian_read_timeout_seconds,
        write_timeout_seconds=settings.obsidian_write_timeout_seconds,
        pool_timeout_seconds=settings.obsidian_pool_timeout_seconds,
    )
    client = ObsidianRestApiHttpClient(
        api_url=settings.obsidian_api_url,
        api_key=settings.obsidian_api_key,
        transport=transport,
    )
    try:
        await client.status()
    except ObsidianClientError:
        return False
    finally:
        await client.close()
    return True
