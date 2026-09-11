"""Obsidian integration package."""

from notes_facade.obsidian.client import ObsidianRestApiHttpClient
from notes_facade.obsidian.errors import (
    ObsidianAuthError,
    ObsidianClientError,
    ObsidianNotFoundError,
    ObsidianUnavailableError,
)
from notes_facade.obsidian.transport import ObsidianHttpTransportClient

__all__ = [
    "ObsidianAuthError",
    "ObsidianClientError",
    "ObsidianHttpTransportClient",
    "ObsidianNotFoundError",
    "ObsidianRestApiHttpClient",
    "ObsidianUnavailableError",
]
