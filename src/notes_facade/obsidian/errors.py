"""Client-specific errors for Obsidian REST API integration."""

from notes_facade.projects.errors import NotesFacadeError


class ObsidianClientError(NotesFacadeError):
    """Base error for Obsidian integration failures."""


class ObsidianAuthError(ObsidianClientError):
    """Raised when Obsidian API rejects credentials."""


class ObsidianUnavailableError(ObsidianClientError):
    """Raised when Obsidian API is unavailable."""


class ObsidianNotFoundError(ObsidianClientError):
    """Raised when the requested API resource does not exist."""
