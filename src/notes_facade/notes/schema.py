"""Notes frontmatter schema and parsing helpers."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import ErrorDetails

from notes_facade.projects.errors import NotesFacadeError

VALIDATION_ERROR_PREVIEW_LIMIT = 3


class NoteTypes(StrEnum):
    """Supported note type values for frontmatter."""

    THOUGHT = "thought"
    TASK = "task"
    PROJECT = "project"
    REFERENCE = "reference"


class NoteStatuses(StrEnum):
    """Supported workflow statuses for frontmatter."""

    INBOX = "inbox"
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    ARCHIVED = "archived"


class FrontmatterValidationError(NotesFacadeError):
    """Raised when note frontmatter does not match domain schema."""

    def __init__(self, *, errors: list[ErrorDetails]) -> None:
        self.errors = errors
        preview = errors[:VALIDATION_ERROR_PREVIEW_LIMIT]
        super().__init__(f"Invalid note frontmatter: {preview}")


class Frontmatter(BaseModel):
    """Canonical frontmatter representation for managed notes."""

    id: str
    type: NoteTypes
    status: NoteStatuses
    created: date
    updated: date
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


def parse_note_document(note_content: str) -> tuple[Frontmatter, str]:
    """Parse markdown note with YAML frontmatter into structured data."""
    parsed_post = frontmatter.loads(note_content)
    metadata = dict(parsed_post.metadata)

    try:
        frontmatter_data = Frontmatter.model_validate(metadata)
    except ValidationError as error:
        raise FrontmatterValidationError(errors=error.errors()) from error

    return frontmatter_data, parsed_post.content


def render_note_document(frontmatter_data: Frontmatter, body: str) -> str:
    """Serialize structured frontmatter and markdown body to note content."""
    metadata = frontmatter_data.model_dump(mode="json")
    frontmatter_only_post = frontmatter.Post(content="", **metadata)
    serialized_frontmatter = frontmatter.dumps(frontmatter_only_post)
    if not serialized_frontmatter.endswith("\n"):
        serialized_frontmatter = f"{serialized_frontmatter}\n"
    return f"{serialized_frontmatter}{body}"
