"""Application service for project-scoped note operations."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import uuid4

import frontmatter
from pydantic import BaseModel, ConfigDict, Field

from notes_facade.notes.paths import (
    InvalidNotePathError,
    NotePathError,
    absolute_to_project_relative_path,
    normalize_project_relative_path,
    sanitize_note_title,
    scoped_vault_path,
)
from notes_facade.notes.schema import (
    Frontmatter,
    NoteStatuses,
    NoteTypes,
    parse_note_document,
    render_note_document,
)
from notes_facade.notes.validation import ProjectNotesValidator, ValidateReport
from notes_facade.obsidian.client import ObsidianRestApiHttpClient
from notes_facade.obsidian.errors import ObsidianClientError, ObsidianNotFoundError
from notes_facade.obsidian.models import (
    JsonValue,
    PatchScopes,
    PatchTargetTypes,
    SearchJsonLogicRequest,
    SearchResultItem,
    SearchSimpleRequest,
    VaultPatchBody,
    VaultPatchRequest,
    VaultPathRequest,
    VaultPutRequest,
)
from notes_facade.projects.errors import NotesFacadeError
from notes_facade.projects.registry import ProjectRegistry
from notes_facade.settings import Settings

INBOX_FOLDER = "00 Inbox"
SYSTEM_TEMPLATES_FOLDER = "_system/templates"
TITLE_PLACEHOLDER = "{{TITLE}}"
TEXT_PLACEHOLDER = "{{TEXT}}"
CONTENT_PLACEHOLDER = "{{CONTENT}}"
LINKS_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
UPDATED_FIELD = "updated"
TYPE_FIELD = "type"
STATUS_FIELD = "status"
REPLACE_OPERATION = "replace"
DELETE_OPERATION = "delete"
APPEND_OPERATION = "append"
PREPEND_OPERATION = "prepend"
MOVE_STEP_READ_SOURCE = "read_source"
MOVE_STEP_COPY_TARGET = "copy_target"
MOVE_STEP_FIND_LINKS = "find_incoming_links"
MOVE_STEP_REWRITE_LINKS = "rewrite_links"
MOVE_STEP_DELETE_SOURCE = "delete_source"
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class InvalidPatchOperationError(NotesFacadeError):
    """Raised when patch operation is not valid for current contract."""


class InvalidMoveOperationError(NotesFacadeError):
    """Raised when move operation receives unsafe source or target paths."""


class CaptureResult(BaseModel):
    """Result payload for a captured note."""

    path: str
    note_id: str = Field(alias="id")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class FindResultItem(BaseModel):
    """Public search result item."""

    path: str
    snippet: str

    model_config = ConfigDict(extra="forbid")


class ReadNoteResult(BaseModel):
    """Result payload for read operation."""

    frontmatter: Frontmatter
    content: str
    backlinks: list[str]
    outlinks: list[str]

    model_config = ConfigDict(extra="forbid")


class SetFieldPatchOperation(BaseModel):
    """Set frontmatter field value."""

    kind: Literal["set_field"]
    field: str
    value: JsonValue

    model_config = ConfigDict(extra="forbid")


class RemoveFieldPatchOperation(BaseModel):
    """Remove frontmatter field."""

    kind: Literal["remove_field"]
    field: str

    model_config = ConfigDict(extra="forbid")


class AppendUnderHeadingPatchOperation(BaseModel):
    """Append content under markdown heading."""

    kind: Literal["append_under_heading"]
    heading: str | list[str]
    content: str

    model_config = ConfigDict(extra="forbid")


class PrependUnderHeadingPatchOperation(BaseModel):
    """Prepend content under markdown heading."""

    kind: Literal["prepend_under_heading"]
    heading: str | list[str]
    content: str

    model_config = ConfigDict(extra="forbid")


class ReplaceBlockPatchOperation(BaseModel):
    """Replace one exact block with another."""

    kind: Literal["replace_block"]
    target: str
    content: str

    model_config = ConfigDict(extra="forbid")


type NotePatchOperation = Annotated[
    SetFieldPatchOperation
    | RemoveFieldPatchOperation
    | AppendUnderHeadingPatchOperation
    | PrependUnderHeadingPatchOperation
    | ReplaceBlockPatchOperation,
    Field(discriminator="kind"),
]


class PatchResult(BaseModel):
    """Result payload for patch operation."""

    path: str
    updated: date

    model_config = ConfigDict(extra="forbid")


class MoveLinkUpdateResult(BaseModel):
    """A single note where incoming links were rewritten."""

    path: str
    replacements: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class MoveNoteResult(BaseModel):
    """Move operation result with progress for partial failures."""

    source_path: str
    target_path: str
    copied: bool
    links_rewritten: list[MoveLinkUpdateResult]
    original_deleted: bool
    success: bool
    failed_step: str | None = None
    error: str | None = None

    model_config = ConfigDict(extra="forbid")


class ReviewItem(BaseModel):
    """Single stale note item for review queue."""

    path: str
    age_days: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class ReviewQueueResult(BaseModel):
    """Review queues grouped by inbox and active statuses."""

    stale_inbox: list[ReviewItem]
    stale_active: list[ReviewItem]

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class _ScopedSearchHit:
    absolute_path: str
    relative_path: str
    snippet: str


@dataclass(frozen=True)
class _DirectBodyBlock:
    heading_path: tuple[str, ...]
    within_index: int
    content: str


class NotesService:
    """Provides project-scoped note workflows over Obsidian REST API."""

    def __init__(
        self,
        project_registry: ProjectRegistry,
        obsidian_client: ObsidianRestApiHttpClient,
        settings: Settings,
    ) -> None:
        self._project_registry = project_registry
        self._obsidian_client = obsidian_client
        self._settings = settings
        self._validator = ProjectNotesValidator(vault_ro_path=self._settings.vault_ro_path)

    async def capture(
        self,
        *,
        project_id: str,
        title: str,
        text: str,
        note_type: NoteTypes,
        tags: list[str] | None = None,
    ) -> CaptureResult:
        """Create a note in project inbox using a type template."""
        project = self._project_registry.resolve(project_id)
        note_relative_path = await self._find_available_inbox_path(
            project_folder=project.folder,
            title=title,
        )
        note_path = scoped_vault_path(project_folder=project.folder, path=note_relative_path)

        template_path = self._template_path(note_type=note_type)
        template_note = await self._obsidian_client.get_note(
            VaultPathRequest(path=template_path),
        )
        template_body = self._extract_template_body(template_note.content)

        today = date.today()
        frontmatter_data = Frontmatter(
            id=str(uuid4()),
            type=note_type,
            status=NoteStatuses.INBOX,
            created=today,
            updated=today,
            tags=tags or [],
            related=[],
        )
        body = self._render_note_body(template_body=template_body, title=title, text=text)
        content = render_note_document(frontmatter_data=frontmatter_data, body=body)
        await self._obsidian_client.put_note(VaultPutRequest(path=note_path, content=content))
        return CaptureResult(path=note_relative_path, id=frontmatter_data.id)

    async def find(
        self,
        *,
        project_id: str,
        query: str,
        note_type: NoteTypes | None = None,
        status: NoteStatuses | None = None,
        limit: int = 20,
    ) -> list[FindResultItem]:
        """Find notes within selected project with optional metadata filters."""
        project = self._project_registry.resolve(project_id)
        simple_response = await self._obsidian_client.search_simple(
            SearchSimpleRequest(query=query),
        )
        simple_hits = self._collect_project_hits(
            items=simple_response.results,
            project_folder=project.folder,
        )

        hits = simple_hits
        if note_type is not None or status is not None:
            jsonlogic_response = await self._obsidian_client.search_jsonlogic(
                SearchJsonLogicRequest(
                    query=self._build_jsonlogic_query(note_type=note_type, status=status),
                )
            )
            jsonlogic_paths = {
                hit.absolute_path
                for hit in self._collect_project_hits(
                    items=jsonlogic_response.results,
                    project_folder=project.folder,
                )
            }
            hits = [hit for hit in simple_hits if hit.absolute_path in jsonlogic_paths]

        return [
            FindResultItem(path=hit.relative_path, snippet=hit.snippet)
            for hit in hits[: max(limit, 0)]
        ]

    async def read_note(
        self,
        *,
        project_id: str,
        path: str,
        section: str | None = None,
    ) -> ReadNoteResult:
        """Read note content, optional section, backlinks, and outlinks in project scope."""
        project = self._project_registry.resolve(project_id)
        note_path = scoped_vault_path(project_folder=project.folder, path=path)
        note_response = await self._obsidian_client.get_note(VaultPathRequest(path=note_path))
        frontmatter_data, body = parse_note_document(note_response.content)
        content = body if section is None else self._extract_section(body=body, section=section)
        note_relative_path = absolute_to_project_relative_path(
            absolute_path=note_path,
            project_folder=project.folder,
        )

        note_name = PurePosixPath(path).stem
        backlinks_response = await self._obsidian_client.search_simple(
            SearchSimpleRequest(query=f"[[{note_name}"),
        )
        backlinks = [
            hit.relative_path
            for hit in self._collect_project_hits(
                items=backlinks_response.results,
                project_folder=project.folder,
            )
            if hit.relative_path != note_relative_path
        ]

        return ReadNoteResult(
            frontmatter=frontmatter_data,
            content=content,
            backlinks=backlinks,
            outlinks=self._extract_outlinks(body=body),
        )

    async def patch(
        self,
        *,
        project_id: str,
        path: str,
        operation: NotePatchOperation,
    ) -> PatchResult:
        """Apply a constrained patch operation and update frontmatter.updated."""
        project = self._project_registry.resolve(project_id)
        scoped_path = scoped_vault_path(project_folder=project.folder, path=path)
        if isinstance(operation, ReplaceBlockPatchOperation):
            patch_body = await self._build_replace_block_patch_body(
                scoped_path=scoped_path,
                operation=operation,
            )
        else:
            patch_body = self._build_patch_body(operation=operation)

        await self._obsidian_client.patch_note(
            VaultPatchRequest(
                path=scoped_path,
                body=patch_body,
            )
        )

        today = date.today()
        await self._obsidian_client.patch_note(
            VaultPatchRequest(
                path=scoped_path,
                body=VaultPatchBody(
                    targetType=PatchTargetTypes.FRONTMATTER,
                    operation=REPLACE_OPERATION,
                    target=UPDATED_FIELD,
                    value=today.isoformat(),
                ),
            )
        )
        return PatchResult(path=normalize_project_relative_path(path), updated=today)

    async def move_note(
        self,
        *,
        project_id: str,
        path: str,
        new_path: str,
    ) -> MoveNoteResult:
        """Move note with ordered copy-rewrite-delete flow in one project scope."""
        project = self._project_registry.resolve(project_id)
        source_path = scoped_vault_path(project_folder=project.folder, path=path)
        target_path = scoped_vault_path(project_folder=project.folder, path=new_path)
        source_relative_path = absolute_to_project_relative_path(
            absolute_path=source_path,
            project_folder=project.folder,
        )
        target_relative_path = absolute_to_project_relative_path(
            absolute_path=target_path,
            project_folder=project.folder,
        )
        result = MoveNoteResult(
            source_path=source_relative_path,
            target_path=target_relative_path,
            copied=False,
            links_rewritten=[],
            original_deleted=False,
            success=False,
        )
        if source_relative_path == target_relative_path:
            return self._mark_move_failure(
                result=result,
                step=MOVE_STEP_READ_SOURCE,
                error=InvalidMoveOperationError("Source and target paths must differ"),
            )

        try:
            source_note = await self._obsidian_client.get_note(
                VaultPathRequest(path=source_path),
            )
        except (ObsidianClientError, NotePathError) as error:
            return self._mark_move_failure(
                result=result,
                step=MOVE_STEP_READ_SOURCE,
                error=error,
            )

        try:
            await self._obsidian_client.put_note(
                VaultPutRequest(path=target_path, content=source_note.content),
            )
            result.copied = True
        except ObsidianClientError as error:
            return self._mark_move_failure(
                result=result,
                step=MOVE_STEP_COPY_TARGET,
                error=error,
            )

        source_name = PurePosixPath(source_relative_path).stem
        target_name = PurePosixPath(target_relative_path).stem
        try:
            search_response = await self._obsidian_client.search_simple(
                SearchSimpleRequest(query=f"[[{source_name}"),
            )
            link_hits = self._collect_project_hits(
                items=search_response.results,
                project_folder=project.folder,
            )
        except ObsidianClientError as error:
            return self._mark_move_failure(
                result=result,
                step=MOVE_STEP_FIND_LINKS,
                error=error,
            )

        for hit in link_hits:
            if hit.absolute_path in {source_path, target_path}:
                continue
            try:
                await self._rewrite_note_links(
                    note_path=hit.absolute_path,
                    old_note_name=source_name,
                    new_note_name=target_name,
                    result=result,
                    relative_path=hit.relative_path,
                )
            except ObsidianClientError as error:
                return self._mark_move_failure(
                    result=result,
                    step=MOVE_STEP_REWRITE_LINKS,
                    error=error,
                )

        try:
            await self._obsidian_client.delete_note(VaultPathRequest(path=source_path))
            result.original_deleted = True
        except ObsidianClientError as error:
            return self._mark_move_failure(
                result=result,
                step=MOVE_STEP_DELETE_SOURCE,
                error=error,
            )

        result.success = True
        return result

    async def review_queue(self, *, project_id: str) -> ReviewQueueResult:
        """Build stale inbox and stale active queues using JsonLogic filters."""
        project = self._project_registry.resolve(project_id)
        today = date.today()
        inbox_cutoff = (
            today - timedelta(days=max(self._settings.review_inbox_days, 0))
        ).isoformat()
        active_cutoff = (
            today - timedelta(days=max(self._settings.review_stale_active_days, 0))
        ).isoformat()

        stale_inbox = await self._collect_stale_notes(
            project_folder=project.folder,
            status=NoteStatuses.INBOX,
            threshold_field="created",
            cutoff_iso=inbox_cutoff,
            today=today,
        )
        stale_active = await self._collect_stale_notes(
            project_folder=project.folder,
            status=NoteStatuses.ACTIVE,
            threshold_field="updated",
            cutoff_iso=active_cutoff,
            today=today,
        )
        return ReviewQueueResult(stale_inbox=stale_inbox, stale_active=stale_active)

    async def validate(self, *, project_id: str) -> ValidateReport:
        """Validate project markdown notes using read-only vault mount."""
        project = self._project_registry.resolve(project_id)
        return self._validator.validate(project_folder=project.folder)

    async def _find_available_inbox_path(
        self,
        *,
        project_folder: str,
        title: str,
    ) -> str:
        sanitized_title = sanitize_note_title(title)
        index = 1
        while True:
            suffix = "" if index == 1 else f" {index}"
            candidate_filename = f"{sanitized_title}{suffix}.md"
            candidate_relative = normalize_project_relative_path(
                posixpath.join(INBOX_FOLDER, candidate_filename),
            )
            candidate_scoped_path = scoped_vault_path(
                project_folder=project_folder,
                path=candidate_relative,
            )
            try:
                await self._obsidian_client.get_note(VaultPathRequest(path=candidate_scoped_path))
            except ObsidianNotFoundError:
                return candidate_relative
            index += 1

    def _collect_project_hits(
        self,
        *,
        items: list[SearchResultItem],
        project_folder: str,
    ) -> list[_ScopedSearchHit]:
        hits: list[_ScopedSearchHit] = []
        for item in items:
            raw_path = item.path or item.filename
            if raw_path is None:
                continue
            try:
                relative_path = absolute_to_project_relative_path(
                    absolute_path=raw_path,
                    project_folder=project_folder,
                )
            except NotePathError:
                continue
            absolute_path = scoped_vault_path(project_folder=project_folder, path=relative_path)
            hits.append(
                _ScopedSearchHit(
                    absolute_path=absolute_path,
                    relative_path=relative_path,
                    snippet=self._extract_snippet(item=item),
                )
            )
        return hits

    def _extract_snippet(self, item: SearchResultItem) -> str:
        if item.snippet:
            return item.snippet
        for context in item.matches:
            if context.context:
                return context.context
        for context in item.context:
            if context.context:
                return context.context
        return ""

    def _template_path(self, note_type: NoteTypes) -> str:
        return f"{SYSTEM_TEMPLATES_FOLDER}/{note_type.value}.md"

    def _extract_template_body(self, content: str) -> str:
        parsed_post = frontmatter.loads(content)
        return str(parsed_post.content)

    def _render_note_body(self, *, template_body: str, title: str, text: str) -> str:
        wrapped_body = template_body.replace(TITLE_PLACEHOLDER, title)
        if TEXT_PLACEHOLDER in wrapped_body:
            return wrapped_body.replace(TEXT_PLACEHOLDER, text)
        if CONTENT_PLACEHOLDER in wrapped_body:
            return wrapped_body.replace(CONTENT_PLACEHOLDER, text)
        if not wrapped_body.strip():
            return text
        return f"{wrapped_body.rstrip()}\n\n{text}"

    def _build_jsonlogic_query(
        self,
        *,
        note_type: NoteTypes | None,
        status: NoteStatuses | None,
    ) -> JsonValue:
        clauses: list[JsonValue] = []
        if note_type is not None:
            clauses.append({"==": [{"var": "frontmatter.type"}, note_type.value]})
        if status is not None:
            clauses.append({"==": [{"var": "frontmatter.status"}, status.value]})
        if not clauses:
            return {}
        if len(clauses) == 1:
            return clauses[0]
        return {"and": clauses}

    def _extract_section(self, *, body: str, section: str) -> str:
        heading = section.strip()
        if not heading:
            return body

        section_start: int | None = None
        section_end = len(body)
        lines = body.splitlines()
        for index, line in enumerate(lines):
            if not line.startswith("#"):
                continue
            header_level = len(line) - len(line.lstrip("#"))
            header_text = line[header_level:].strip()
            if section_start is None and header_text == heading:
                section_start = index
                target_level = header_level
                continue
            if section_start is not None and header_level <= target_level:
                section_end = index
                break

        if section_start is None:
            return ""

        return "\n".join(lines[section_start:section_end]).strip()

    def _extract_outlinks(self, *, body: str) -> list[str]:
        outlinks: list[str] = []
        seen: set[str] = set()
        for raw_target in LINKS_PATTERN.findall(body):
            target = raw_target.strip()
            if not target:
                continue
            normalized_target = self._normalize_wikilink_target(target)
            if normalized_target in seen:
                continue
            seen.add(normalized_target)
            outlinks.append(normalized_target)
        return outlinks

    def _normalize_wikilink_target(self, target: str) -> str:
        normalized = target.replace("\\", "/").strip()
        if not normalized:
            raise InvalidNotePathError("Wikilink target is empty")
        normalized_path = posixpath.normpath(normalized)
        if normalized_path.endswith(".md"):
            return normalized_path[: -len(".md")]
        return normalized_path

    def _build_patch_body(self, *, operation: NotePatchOperation) -> VaultPatchBody:
        if isinstance(operation, SetFieldPatchOperation):
            value = self._validate_frontmatter_patch_value(
                field=operation.field,
                value=operation.value,
            )
            return VaultPatchBody(
                targetType=PatchTargetTypes.FRONTMATTER,
                operation=REPLACE_OPERATION,
                target=operation.field,
                value=value,
            )
        if isinstance(operation, RemoveFieldPatchOperation):
            return VaultPatchBody(
                targetType=PatchTargetTypes.FRONTMATTER,
                operation=DELETE_OPERATION,
                scope=PatchScopes.MARKER_AND_CONTENT,
                target=operation.field,
            )
        if isinstance(operation, AppendUnderHeadingPatchOperation):
            return VaultPatchBody(
                targetType=PatchTargetTypes.HEADING,
                operation=APPEND_OPERATION,
                scope=PatchScopes.CONTENT,
                target=self._build_heading_target(heading=operation.heading),
                content=operation.content,
            )
        if isinstance(operation, PrependUnderHeadingPatchOperation):
            return VaultPatchBody(
                targetType=PatchTargetTypes.HEADING,
                operation=PREPEND_OPERATION,
                scope=PatchScopes.CONTENT,
                target=self._build_heading_target(heading=operation.heading),
                content=operation.content,
            )
        if isinstance(operation, ReplaceBlockPatchOperation):
            self._validate_replace_block_operation(operation=operation)
            return VaultPatchBody(
                targetType=PatchTargetTypes.BLOCK,
                operation=REPLACE_OPERATION,
                scope=PatchScopes.CONTENT,
                target=operation.target,
                content=operation.content,
            )
        raise InvalidPatchOperationError("Unsupported patch operation")

    def _validate_replace_block_operation(
        self,
        *,
        operation: ReplaceBlockPatchOperation,
    ) -> None:
        if not operation.target.strip():
            raise InvalidPatchOperationError("replace_block target cannot be empty")
        if operation.target == operation.content:
            raise InvalidPatchOperationError("replace_block target and content are equal")
        if operation.target.startswith("---\n"):
            raise InvalidPatchOperationError("Full-file replacement is forbidden")

    async def _build_replace_block_patch_body(
        self,
        *,
        scoped_path: str,
        operation: ReplaceBlockPatchOperation,
    ) -> VaultPatchBody:
        self._validate_replace_block_operation(operation=operation)
        note_response = await self._obsidian_client.get_note(
            VaultPathRequest(path=scoped_path),
        )
        _, body = parse_note_document(note_response.content)
        block = self._find_direct_body_block(body=body, target=operation.target)
        if block is None:
            raise InvalidPatchOperationError("replace_block target was not found")
        section_body = self._extract_heading_section_body(
            body=body,
            heading_path=block.heading_path,
        )
        if section_body is None:
            raise InvalidPatchOperationError("replace_block section was not found")
        replaced_section = section_body.replace(block.content, operation.content, 1)
        return VaultPatchBody(
            targetType=PatchTargetTypes.HEADING,
            operation=REPLACE_OPERATION,
            scope=PatchScopes.CONTENT,
            target=list(block.heading_path),
            content=replaced_section,
        )

    def _build_heading_target(self, *, heading: str | list[str]) -> list[str]:
        if isinstance(heading, list):
            return [item.strip() for item in heading if item.strip()]
        heading_value = heading.strip()
        if not heading_value:
            return []
        return [heading_value]

    def _find_direct_body_block(
        self,
        *,
        body: str,
        target: str,
    ) -> _DirectBodyBlock | None:
        for block in self._extract_direct_body_blocks(body=body):
            if block.content == target:
                return block
        return None

    def _extract_heading_section_body(
        self,
        *,
        body: str,
        heading_path: tuple[str, ...],
    ) -> str | None:
        lines = body.splitlines()
        section_start: int | None = None
        target_level = len(heading_path)
        path: list[str] = []
        for index, line in enumerate(lines):
            if not line.startswith("#"):
                continue
            level = len(line) - len(line.lstrip("#"))
            heading_text = line[level:].strip()
            path = path[: level - 1]
            path.append(heading_text)
            if tuple(path) == heading_path:
                section_start = index + 1
                continue
            if section_start is not None and level <= target_level:
                return "\n".join(lines[section_start:index]).rstrip()
        if section_start is None:
            return None
        return "\n".join(lines[section_start:]).rstrip()

    def _validate_frontmatter_patch_value(self, *, field: str, value: JsonValue) -> JsonValue:
        normalized_field = field.strip().lower()
        if normalized_field == TYPE_FIELD:
            if not isinstance(value, str):
                raise InvalidPatchOperationError("type must be a string enum value")
            try:
                return NoteTypes(value).value
            except ValueError as error:
                raise InvalidPatchOperationError("Invalid type value") from error
        if normalized_field == STATUS_FIELD:
            if not isinstance(value, str):
                raise InvalidPatchOperationError("status must be a string enum value")
            try:
                return NoteStatuses(value).value
            except ValueError as error:
                raise InvalidPatchOperationError("Invalid status value") from error
        return value

    async def _rewrite_note_links(
        self,
        *,
        note_path: str,
        old_note_name: str,
        new_note_name: str,
        result: MoveNoteResult,
        relative_path: str,
    ) -> None:
        note_response = await self._obsidian_client.get_note(VaultPathRequest(path=note_path))
        patches = self._build_heading_block_link_patches(
            content=note_response.content,
            old_note_name=old_note_name,
            new_note_name=new_note_name,
        )
        for patch in patches:
            await self._obsidian_client.patch_note(
                VaultPatchRequest(
                    path=note_path,
                    body=VaultPatchBody(
                        targetType=PatchTargetTypes.HEADING,
                        operation=REPLACE_OPERATION,
                        target=list(patch.heading_path),
                        within=patch.within_index,
                        content=patch.content,
                    ),
                )
            )
            self._increment_rewrite_progress(
                result=result,
                relative_path=relative_path,
            )

    def _build_heading_block_link_patches(
        self,
        *,
        content: str,
        old_note_name: str,
        new_note_name: str,
    ) -> list[_DirectBodyBlock]:
        body = self._extract_raw_note_body(content=content)
        direct_blocks = self._extract_direct_body_blocks(body=body)
        pattern = re.compile(
            r"\[\[" + re.escape(old_note_name) + r"(?P<suffix>(?:#[^\]|]+)?(?:\|[^\]]+)?)\]\]"
        )
        patches: list[_DirectBodyBlock] = []
        for block in direct_blocks:
            replaced_content = pattern.sub(
                lambda match: f"[[{new_note_name}{match.group('suffix') or ''}]]",
                block.content,
            )
            if replaced_content == block.content:
                continue
            patches.append(
                _DirectBodyBlock(
                    heading_path=block.heading_path,
                    within_index=block.within_index,
                    content=replaced_content,
                )
            )
        return patches

    def _extract_direct_body_blocks(self, *, body: str) -> list[_DirectBodyBlock]:
        blocks: list[_DirectBodyBlock] = []
        heading_path: list[str] = []
        section_block_counts: dict[tuple[str, ...], int] = {}
        current_lines: list[str] = []
        current_heading_path: tuple[str, ...] = tuple()

        def flush_current_lines() -> None:
            nonlocal current_lines
            if not current_lines:
                return
            block_content = "\n".join(current_lines)
            current_lines = []
            within_index = section_block_counts.get(current_heading_path, 0)
            section_block_counts[current_heading_path] = within_index + 1
            blocks.append(
                _DirectBodyBlock(
                    heading_path=current_heading_path,
                    within_index=within_index,
                    content=block_content,
                )
            )

        for line in body.splitlines():
            heading_match = HEADING_PATTERN.match(line)
            if heading_match is not None:
                flush_current_lines()
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip()
                heading_path = heading_path[: level - 1]
                heading_path.append(heading_text)
                current_heading_path = tuple(heading_path)
                continue
            if not line.strip():
                flush_current_lines()
                current_heading_path = tuple(heading_path)
                continue
            if not current_lines:
                current_heading_path = tuple(heading_path)
            current_lines.append(line)

        flush_current_lines()
        return blocks

    def _extract_raw_note_body(self, *, content: str) -> str:
        lines = content.splitlines(keepends=True)
        if not lines:
            return ""
        if lines[0].strip() != "---":
            return content
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return "".join(lines[index + 1 :])
        return content

    def _increment_rewrite_progress(
        self,
        *,
        result: MoveNoteResult,
        relative_path: str,
    ) -> None:
        for index, rewritten in enumerate(result.links_rewritten):
            if rewritten.path != relative_path:
                continue
            result.links_rewritten[index] = MoveLinkUpdateResult(
                path=relative_path,
                replacements=rewritten.replacements + 1,
            )
            return
        result.links_rewritten.append(
            MoveLinkUpdateResult(path=relative_path, replacements=1),
        )

    def _mark_move_failure(
        self,
        *,
        result: MoveNoteResult,
        step: str,
        error: Exception,
    ) -> MoveNoteResult:
        result.failed_step = step
        result.error = str(error)
        return result

    async def _collect_stale_notes(
        self,
        *,
        project_folder: str,
        status: NoteStatuses,
        threshold_field: str,
        cutoff_iso: str,
        today: date,
    ) -> list[ReviewItem]:
        query: JsonValue = {
            "and": [
                {"==": [{"var": "frontmatter.status"}, status.value]},
                {"<=": [{"var": f"frontmatter.{threshold_field}"}, cutoff_iso]},
            ]
        }
        search_response = await self._obsidian_client.search_jsonlogic(
            SearchJsonLogicRequest(query=query),
        )
        scoped_hits = self._collect_project_hits(
            items=search_response.results,
            project_folder=project_folder,
        )
        stale_items: list[ReviewItem] = []
        for hit in scoped_hits:
            note_response = await self._obsidian_client.get_note(
                VaultPathRequest(path=hit.absolute_path),
            )
            frontmatter_data, _ = parse_note_document(note_response.content)
            date_value = (
                frontmatter_data.created
                if threshold_field == "created"
                else frontmatter_data.updated
            )
            stale_items.append(
                ReviewItem(
                    path=hit.relative_path,
                    age_days=(today - date_value).days,
                )
            )
        return stale_items
