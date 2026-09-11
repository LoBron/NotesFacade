"""Entrypoint assembling dependencies and serving MCP over HTTP."""

from __future__ import annotations

import uvicorn
from starlette.types import ASGIApp

from notes_facade.health import register_health_route
from notes_facade.mcp.server import mcp
from notes_facade.mcp.tools import register_notes_tools
from notes_facade.notes.service import NotesService
from notes_facade.obsidian.client import ObsidianRestApiHttpClient
from notes_facade.obsidian.transport import ObsidianHttpTransportClient
from notes_facade.projects.registry import ProjectRegistry
from notes_facade.settings import get_settings

CONTAINER_BIND_HOST = "0.0.0.0"
CONTAINER_BIND_PORT = 8000


def build_app() -> ASGIApp:
    """Build ASGI app with settings, registry, clients, service, tools, and health route."""
    settings = get_settings()
    registry = ProjectRegistry(settings.projects_config_path)
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
    service = NotesService(
        project_registry=registry,
        obsidian_client=client,
        settings=settings,
    )

    register_notes_tools(service=service, mcp_server=mcp)
    register_health_route(mcp_server=mcp, settings=settings)
    return mcp.http_app()


app = build_app()


if __name__ == "__main__":
    uvicorn.run(app, host=CONTAINER_BIND_HOST, port=CONTAINER_BIND_PORT)
