"""Path utilities for project-scoped note operations."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from pathlib import PurePosixPath

from notes_facade.projects.errors import NotesFacadeError

INVALID_FILENAME_CHARS_PATTERN = re.compile(r'[\\/:*?"<>|]+')
WHITESPACE_PATTERN = re.compile(r"\s+")
MARKDOWN_EXTENSION = ".md"
DEFAULT_TITLE = "Untitled"
ROOT_VAULT_PREFIX = "/vault/"


class NotePathError(NotesFacadeError):
    """Base error for note path normalization and scoping failures."""


class InvalidNotePathError(NotePathError):
    """Raised when note path is invalid or unsafe."""


class PathOutsideProjectError(NotePathError):
    """Raised when path does not belong to the selected project folder."""


def sanitize_note_title(title: str) -> str:
    """Create a filesystem-safe display title for a markdown filename."""
    sanitized = INVALID_FILENAME_CHARS_PATTERN.sub(" ", title)
    sanitized = WHITESPACE_PATTERN.sub(" ", sanitized).strip().strip(".")
    if not sanitized:
        return DEFAULT_TITLE
    return sanitized


def build_note_path(
    *,
    title: str,
    folder: str,
    existing_paths: Iterable[str] | None = None,
) -> str:
    """Build deterministic project-relative note path with deduplication suffixes."""
    normalized_folder = normalize_project_relative_path(folder)
    base_name = sanitize_note_title(title)
    occupied_paths = {normalize_project_relative_path(path) for path in (existing_paths or ())}

    index = 1
    while True:
        suffix = "" if index == 1 else f" {index}"
        filename = f"{base_name}{suffix}{MARKDOWN_EXTENSION}"
        candidate = normalize_project_relative_path(posixpath.join(normalized_folder, filename))
        if candidate not in occupied_paths:
            return candidate
        index += 1


def normalize_project_relative_path(path: str) -> str:
    """Normalize and validate a path relative to project folder."""
    if not path:
        raise InvalidNotePathError("Path is empty")

    normalized_slashes = path.replace("\\", "/").strip()
    if not normalized_slashes:
        raise InvalidNotePathError("Path is empty")
    if normalized_slashes.startswith("/"):
        raise InvalidNotePathError("Absolute paths are not allowed")

    source_parts = PurePosixPath(normalized_slashes).parts
    if any(part == ".." for part in source_parts):
        raise InvalidNotePathError("Path traversal is not allowed")

    normalized = posixpath.normpath(normalized_slashes)
    if normalized in {".", ""}:
        raise InvalidNotePathError("Path must point to a file or folder")

    normalized_parts = PurePosixPath(normalized).parts
    if any(part == ".." for part in normalized_parts):
        raise InvalidNotePathError("Path traversal is not allowed")

    _validate_path_parts(normalized_parts)
    return normalized


def scoped_vault_path(*, project_folder: str, path: str) -> str:
    """Build project-scoped vault path and ensure it stays inside project folder."""
    normalized_project_folder = normalize_project_relative_path(project_folder)
    normalized_path = normalize_project_relative_path(path)
    full_path = normalize_project_relative_path(
        posixpath.join(normalized_project_folder, normalized_path)
    )
    project_prefix = f"{normalized_project_folder}/"
    if not full_path.startswith(project_prefix):
        raise PathOutsideProjectError("Path resolves outside the project scope")
    return full_path


def absolute_to_project_relative_path(*, absolute_path: str, project_folder: str) -> str:
    """Convert absolute vault path to validated path relative to project folder."""
    if not absolute_path:
        raise InvalidNotePathError("Path is empty")

    normalized_input = absolute_path.replace("\\", "/").strip()
    if normalized_input.startswith(ROOT_VAULT_PREFIX):
        normalized_input = normalized_input[len(ROOT_VAULT_PREFIX) :]
    elif normalized_input.startswith("/"):
        normalized_input = normalized_input.lstrip("/")

    normalized_input = normalize_project_relative_path(normalized_input)
    normalized_project_folder = normalize_project_relative_path(project_folder)
    project_prefix = f"{normalized_project_folder}/"
    if not normalized_input.startswith(project_prefix):
        raise PathOutsideProjectError("Path does not belong to project folder")

    relative_path = normalized_input[len(project_prefix) :]
    if not relative_path:
        raise InvalidNotePathError("Path must point to a note within project folder")
    return normalize_project_relative_path(relative_path)


def _validate_path_parts(parts: tuple[str, ...]) -> None:
    for part in parts:
        if part in {"", "."}:
            raise InvalidNotePathError("Path contains invalid segment")
        if part == "_system":
            raise InvalidNotePathError("Access to _system is forbidden")
        if part.startswith("."):
            raise InvalidNotePathError("Hidden files and directories are forbidden")
