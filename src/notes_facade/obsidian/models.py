"""Pydantic models for Obsidian Local REST API endpoints."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyString = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | dict[str, JsonValue] | list[JsonValue]


class PatchTargetTypes(StrEnum):
    """Supported PATCH target types for note updates."""

    HEADING = "heading"
    FRONTMATTER = "frontmatter"
    BLOCK = "block"


class VaultPathRequest(BaseModel):
    """Request model for endpoints that require a vault path."""

    path: NonEmptyString

    model_config = ConfigDict(extra="forbid")


class VaultPutRequest(VaultPathRequest):
    """Request model for PUT /vault/{path}."""

    content: str


class VaultPatchBody(BaseModel):
    """PATCH body passed to /vault/{path}."""

    target_type: PatchTargetTypes = Field(alias="targetType")
    operation: NonEmptyString
    target: str | list[str] | None = None
    within: int | None = Field(default=None, ge=0)
    content: str | None = None
    value: Any | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class VaultPatchRequest(VaultPathRequest):
    """Request model for PATCH /vault/{path}."""

    body: VaultPatchBody


class VaultNoteResponse(BaseModel):
    """Structured response for note content read from Obsidian."""

    path: NonEmptyString
    content: str

    model_config = ConfigDict(extra="forbid")


class VaultMutationResponse(BaseModel):
    """Structured response for PUT/PATCH/DELETE operations."""

    path: NonEmptyString
    status_code: int
    message: str | None = None

    model_config = ConfigDict(extra="forbid")


class SearchSimpleRequest(BaseModel):
    """Request model for POST /search/simple/."""

    query: NonEmptyString
    context_length: int | None = Field(default=None, alias="contextLength", ge=0)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SearchJsonLogicRequest(BaseModel):
    """Request model for POST /search/."""

    query: JsonValue

    model_config = ConfigDict(extra="forbid")


class SearchMatchRange(BaseModel):
    """Character range of a match inside context string."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class SearchResultContext(BaseModel):
    """Context fragment for a search hit."""

    context: str | None = None
    match: SearchMatchRange | None = None

    model_config = ConfigDict(extra="forbid")


class SearchResultItem(BaseModel):
    """Normalized search result item returned by Obsidian API."""

    filename: str | None = None
    score: float | None = None
    matches: list[SearchResultContext] = Field(default_factory=list)
    result: JsonValue | None = None

    # Tolerate normalized/internal fields used by existing facade flow.
    path: str | None = None
    snippet: str | None = None
    context: list[SearchResultContext] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class SearchResponse(BaseModel):
    """Structured response for both search endpoints."""

    results: list[SearchResultItem]

    model_config = ConfigDict(extra="forbid")


class ObsidianStatusResponse(BaseModel):
    """Structured response for GET / status endpoint."""

    status_code: int
    body: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
