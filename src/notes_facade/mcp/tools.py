"""MCP tools registration for NotesService operations."""

from __future__ import annotations

from fastmcp import FastMCP

from notes_facade.notes.schema import NoteStatuses, NoteTypes
from notes_facade.notes.service import (
    CaptureResult,
    FindResultItem,
    MoveNoteResult,
    NotePatchOperation,
    NotesService,
    PatchResult,
    ReadNoteResult,
    ReviewQueueResult,
)
from notes_facade.notes.validation import ValidateReport
from notes_facade.projects.errors import UNKNOWN_PROJECT_ERROR_MESSAGE, UnknownProjectError


def register_notes_tools(*, service: NotesService, mcp_server: FastMCP) -> None:
    """Register all Notes Facade MCP tools as thin wrappers over NotesService."""

    @mcp_server.tool
    async def capture(
        project_id: str,
        title: str,
        text: str,
        type: NoteTypes,
        tags: list[str] | None = None,
    ) -> CaptureResult:
        """Создать заметку во входящих проекта по шаблону типа."""
        try:
            return await service.capture(
                project_id=project_id,
                title=title,
                text=text,
                note_type=type,
                tags=tags,
            )
        except UnknownProjectError as error:
            raise ValueError(UNKNOWN_PROJECT_ERROR_MESSAGE) from error

    @mcp_server.tool
    async def find(
        project_id: str,
        query: str,
        type: NoteTypes | None = None,
        status: NoteStatuses | None = None,
        limit: int = 20,
    ) -> list[FindResultItem]:
        """Найти заметки проекта по тексту и необязательным фильтрам frontmatter."""
        try:
            return await service.find(
                project_id=project_id,
                query=query,
                note_type=type,
                status=status,
                limit=limit,
            )
        except UnknownProjectError as error:
            raise ValueError(UNKNOWN_PROJECT_ERROR_MESSAGE) from error

    @mcp_server.tool
    async def read_note(
        project_id: str,
        path: str,
        section: str | None = None,
    ) -> ReadNoteResult:
        """Прочитать заметку, опционально секцию, обратные ссылки и outlinks."""
        try:
            return await service.read_note(project_id=project_id, path=path, section=section)
        except UnknownProjectError as error:
            raise ValueError(UNKNOWN_PROJECT_ERROR_MESSAGE) from error

    @mcp_server.tool
    async def patch(
        project_id: str,
        path: str,
        op: NotePatchOperation,
    ) -> PatchResult:
        """Точечно изменить заметку через ограниченные patch-операции."""
        try:
            return await service.patch(project_id=project_id, path=path, operation=op)
        except UnknownProjectError as error:
            raise ValueError(UNKNOWN_PROJECT_ERROR_MESSAGE) from error

    @mcp_server.tool
    async def move_note(
        project_id: str,
        path: str,
        new_path: str,
    ) -> MoveNoteResult:
        """Переместить заметку в проекте с переписыванием входящих wikilinks."""
        try:
            return await service.move_note(project_id=project_id, path=path, new_path=new_path)
        except UnknownProjectError as error:
            raise ValueError(UNKNOWN_PROJECT_ERROR_MESSAGE) from error

    @mcp_server.tool
    async def review_queue(project_id: str) -> ReviewQueueResult:
        """Вернуть очереди ревью: просроченный inbox и stale active заметки."""
        try:
            return await service.review_queue(project_id=project_id)
        except UnknownProjectError as error:
            raise ValueError(UNKNOWN_PROJECT_ERROR_MESSAGE) from error

    @mcp_server.tool
    async def validate(project_id: str) -> ValidateReport:
        """Провалидировать заметки проекта на схему, ссылки и дубликаты id."""
        try:
            return await service.validate(project_id=project_id)
        except UnknownProjectError as error:
            raise ValueError(UNKNOWN_PROJECT_ERROR_MESSAGE) from error
