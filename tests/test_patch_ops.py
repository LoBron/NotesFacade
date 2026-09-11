import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from notes_facade.notes.service import (
    AppendUnderHeadingPatchOperation,
    InvalidPatchOperationError,
    NotesService,
    PrependUnderHeadingPatchOperation,
    RemoveFieldPatchOperation,
    ReplaceBlockPatchOperation,
    SetFieldPatchOperation,
    _DirectBodyBlock,
)
from notes_facade.obsidian.models import PatchScopes, PatchTargetTypes, VaultNoteResponse
from notes_facade.projects.models import Project


def _build_service(obsidian_client: object) -> NotesService:
    registry = Mock()
    registry.resolve.return_value = Project(id="project-1", name="Personal", folder="personal")
    settings = SimpleNamespace(
        vault_ro_path=Path("/tmp"),
        review_inbox_days=7,
        review_stale_active_days=30,
    )
    return NotesService(
        project_registry=registry,
        obsidian_client=obsidian_client,
        settings=settings,
    )


def _split_frontmatter_and_body(document_content: str) -> tuple[list[str], list[str]]:
    lines = document_content.splitlines()
    if not lines or lines[0] != "---":
        return [], lines
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return lines[1:index], lines[index + 1 :]
    return [], lines


def _join_frontmatter_and_body(frontmatter_lines: list[str], body_lines: list[str]) -> str:
    if not frontmatter_lines:
        return "\n".join(body_lines)
    return "\n".join(["---", *frontmatter_lines, "---", *body_lines])


def _heading_path_matches(lines: list[str], heading_path: list[str]) -> tuple[int, int] | None:
    path: list[str] = []
    match_start: int | None = None
    match_level = 0
    for index, line in enumerate(lines):
        if not line.startswith("#"):
            continue
        level = len(line) - len(line.lstrip("#"))
        heading = line[level:].strip()
        path = path[: level - 1]
        path.append(heading)
        if path == heading_path:
            match_start = index
            match_level = level
            continue
        if match_start is not None and level <= match_level:
            return match_start, index
    if match_start is None:
        return None
    return match_start, len(lines)


class _InMemoryPatchClient:
    def __init__(self, initial_content: str, *, note_path: str = "personal/10 Notes/a.md") -> None:
        self.note_path = note_path
        self.content = initial_content
        self.patch_note = AsyncMock(side_effect=self._patch_note)

    async def get_note(self, request):
        assert request.path == self.note_path
        return VaultNoteResponse(path=request.path, content=self.content)

    async def _patch_note(self, request):
        assert request.path == self.note_path
        body = request.body
        if body.target_type == PatchTargetTypes.FRONTMATTER and body.operation == "delete":
            self.content = self._delete_frontmatter_key(self.content, body.target)
        elif body.target_type == PatchTargetTypes.FRONTMATTER and body.operation == "replace":
            self.content = self._replace_frontmatter_key(self.content, body.target, body.value)
        elif body.target_type == PatchTargetTypes.HEADING and body.operation in {
            "append",
            "prepend",
        }:
            self.content = self._mutate_heading_body(
                self.content,
                heading_path=list(body.target or []),
                operation=body.operation,
                new_content=body.content or "",
            )
        elif body.target_type == PatchTargetTypes.HEADING and body.operation == "replace":
            self.content = self._replace_heading_section(
                self.content,
                heading_path=list(body.target or []),
                new_content=body.content or "",
            )
        elif body.target_type == PatchTargetTypes.BLOCK and body.operation == "replace":
            self.content = self.content.replace(body.target or "", body.content or "", 1)
        return None

    def _delete_frontmatter_key(self, content: str, key: str | list[str] | None) -> str:
        assert isinstance(key, str)
        frontmatter_lines, body_lines = _split_frontmatter_and_body(content)
        frontmatter_lines = [line for line in frontmatter_lines if not line.startswith(f"{key}:")]
        return _join_frontmatter_and_body(frontmatter_lines, body_lines)

    def _replace_frontmatter_key(
        self,
        content: str,
        key: str | list[str] | None,
        value,
    ) -> str:
        assert isinstance(key, str)
        frontmatter_lines, body_lines = _split_frontmatter_and_body(content)
        rendered_value = "null" if value is None else str(value)
        replaced = False
        new_lines: list[str] = []
        for line in frontmatter_lines:
            if line.startswith(f"{key}:"):
                new_lines.append(f"{key}: {rendered_value}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"{key}: {rendered_value}")
        return _join_frontmatter_and_body(new_lines, body_lines)

    def _mutate_heading_body(
        self,
        current_content: str,
        *,
        heading_path: list[str],
        operation: str,
        new_content: str,
    ) -> str:
        frontmatter_lines, body_lines = _split_frontmatter_and_body(current_content)
        match = _heading_path_matches(body_lines, heading_path)
        assert match is not None
        start, end = match
        insert_at = end
        if operation == "prepend":
            insert_at = start + 1
        body_lines = body_lines[:insert_at] + [new_content] + body_lines[insert_at:]
        return _join_frontmatter_and_body(frontmatter_lines, body_lines)

    def _replace_heading_section(
        self,
        current_content: str,
        *,
        heading_path: list[str],
        new_content: str,
    ) -> str:
        frontmatter_lines, body_lines = _split_frontmatter_and_body(current_content)
        match = _heading_path_matches(body_lines, heading_path)
        assert match is not None
        start, end = match
        rebuilt_body = body_lines[: start + 1] + new_content.splitlines() + body_lines[end:]
        return _join_frontmatter_and_body(frontmatter_lines, rebuilt_body)

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
            heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
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


@pytest.mark.asyncio
async def test_patch_builds_frontmatter_request_and_updates_updated_field():
    obsidian_client = Mock()
    obsidian_client.patch_note = AsyncMock()
    service = _build_service(obsidian_client=obsidian_client)

    operation = SetFieldPatchOperation(kind="set_field", field="status", value="active")
    result = await service.patch(project_id="project-1", path="10 Notes/a.md", operation=operation)

    assert result.path == "10 Notes/a.md"
    assert obsidian_client.patch_note.await_count == 2

    first_call = obsidian_client.patch_note.await_args_list[0].args[0]
    assert first_call.path == "personal/10 Notes/a.md"
    assert first_call.body.target_type == PatchTargetTypes.FRONTMATTER
    assert first_call.body.target == "status"
    assert first_call.body.operation == "replace"
    assert first_call.body.scope == PatchScopes.CONTENT
    assert first_call.body.value == "active"

    second_call = obsidian_client.patch_note.await_args_list[1].args[0]
    assert second_call.body.target == "updated"
    assert second_call.body.target_type == PatchTargetTypes.FRONTMATTER
    assert second_call.body.operation == "replace"
    assert second_call.body.scope == PatchScopes.CONTENT
    assert second_call.body.value == result.updated.isoformat()


@pytest.mark.asyncio
async def test_patch_rejects_invalid_enum_before_http_call():
    obsidian_client = Mock()
    obsidian_client.patch_note = AsyncMock()
    service = _build_service(obsidian_client=obsidian_client)

    invalid_status = SetFieldPatchOperation(kind="set_field", field="status", value="not-valid")
    invalid_type = SetFieldPatchOperation(kind="set_field", field="type", value="not-valid")

    with pytest.raises(InvalidPatchOperationError, match="Invalid status value"):
        await service.patch(project_id="project-1", path="10 Notes/a.md", operation=invalid_status)
    with pytest.raises(InvalidPatchOperationError, match="Invalid type value"):
        await service.patch(project_id="project-1", path="10 Notes/a.md", operation=invalid_type)

    obsidian_client.patch_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_builds_remove_field_delete_request():
    obsidian_client = Mock()
    obsidian_client.patch_note = AsyncMock()
    service = _build_service(obsidian_client=obsidian_client)

    operation = RemoveFieldPatchOperation(kind="remove_field", field="related")
    await service.patch(project_id="project-1", path="10 Notes/a.md", operation=operation)

    first_call = obsidian_client.patch_note.await_args_list[0].args[0]
    assert first_call.body.target_type == PatchTargetTypes.FRONTMATTER
    assert first_call.body.operation == "delete"
    assert first_call.body.scope == PatchScopes.MARKER_AND_CONTENT
    assert first_call.body.target == "related"
    assert first_call.body.value is None


@pytest.mark.asyncio
async def test_patch_builds_append_under_heading_request():
    obsidian_client = Mock()
    obsidian_client.patch_note = AsyncMock()
    service = _build_service(obsidian_client=obsidian_client)

    operation = AppendUnderHeadingPatchOperation(
        kind="append_under_heading",
        heading=["Links"],
        content="- [[Ref]]",
    )
    await service.patch(project_id="project-1", path="10 Notes/a.md", operation=operation)

    first_call = obsidian_client.patch_note.await_args_list[0].args[0]
    assert first_call.body.target_type == PatchTargetTypes.HEADING
    assert first_call.body.operation == "append"
    assert first_call.body.scope == PatchScopes.CONTENT
    assert first_call.body.target == ["Links"]
    assert first_call.body.content == "- [[Ref]]"


@pytest.mark.asyncio
async def test_patch_builds_prepend_under_heading_request():
    obsidian_client = Mock()
    obsidian_client.patch_note = AsyncMock()
    service = _build_service(obsidian_client=obsidian_client)

    operation = PrependUnderHeadingPatchOperation(
        kind="prepend_under_heading",
        heading=["Links", "Nested"],
        content="First line",
    )
    await service.patch(project_id="project-1", path="10 Notes/a.md", operation=operation)

    first_call = obsidian_client.patch_note.await_args_list[0].args[0]
    assert first_call.body.target_type == PatchTargetTypes.HEADING
    assert first_call.body.operation == "prepend"
    assert first_call.body.scope == PatchScopes.CONTENT
    assert first_call.body.target == ["Links", "Nested"]
    assert first_call.body.content == "First line"


@pytest.mark.asyncio
async def test_patch_builds_replace_block_request():
    obsidian_client = Mock()
    obsidian_client.get_note = AsyncMock(
        return_value=VaultNoteResponse(
            path="personal/10 Notes/a.md",
            content=(
                "---\n"
                "id: note-1\n"
                "type: thought\n"
                "status: inbox\n"
                "created: 2026-09-10\n"
                "updated: 2026-09-10\n"
                "tags: []\n"
                "related: []\n"
                "---\n"
                "# Note\n"
                "## Section\n"
                "First block\n"
                "\n"
                "Target block\n"
                "\n"
                "Last block\n"
            ),
        )
    )
    obsidian_client.patch_note = AsyncMock()
    service = _build_service(obsidian_client=obsidian_client)

    operation = ReplaceBlockPatchOperation(
        kind="replace_block",
        target="Target block",
        content="new block",
    )
    await service.patch(project_id="project-1", path="10 Notes/a.md", operation=operation)

    obsidian_client.get_note.assert_awaited_once()
    first_call = obsidian_client.patch_note.await_args_list[0].args[0]
    assert first_call.body.target_type == PatchTargetTypes.HEADING
    assert first_call.body.operation == "replace"
    assert first_call.body.scope == PatchScopes.CONTENT
    assert first_call.body.target == ["Note", "Section"]
    assert first_call.body.within is None
    assert first_call.body.content == "First block\n\nnew block\n\nLast block"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "content", "message"),
    [
        ("", "new block", "replace_block target cannot be empty"),
        ("same", "same", "replace_block target and content are equal"),
        ("---\nbody", "replacement", "Full-file replacement is forbidden"),
    ],
)
async def test_replace_block_guards_run_before_http_call(
    target: str,
    content: str,
    message: str,
):
    obsidian_client = Mock()
    obsidian_client.patch_note = AsyncMock()
    service = _build_service(obsidian_client=obsidian_client)

    operation = ReplaceBlockPatchOperation(kind="replace_block", target=target, content=content)

    with pytest.raises(InvalidPatchOperationError, match=message):
        await service.patch(project_id="project-1", path="10 Notes/a.md", operation=operation)

    obsidian_client.patch_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_remove_field_deletes_frontmatter_key_from_document():
    initial_content = (
        "---\n"
        "id: note-1\n"
        "type: thought\n"
        "status: inbox\n"
        "created: 2026-09-10\n"
        "updated: 2026-09-10\n"
        "tags: [a, b]\n"
        "related: []\n"
        "---\n"
        "# Note\n"
        "Body line\n"
    )
    obsidian_client = _InMemoryPatchClient(initial_content=initial_content)
    service = _build_service(obsidian_client=obsidian_client)

    operation = RemoveFieldPatchOperation(kind="remove_field", field="tags")
    result = await service.patch(project_id="project-1", path="10 Notes/a.md", operation=operation)

    frontmatter_lines, body_lines = _split_frontmatter_and_body(obsidian_client.content)
    assert result.path == "10 Notes/a.md"
    assert not any(line.startswith("tags:") for line in frontmatter_lines)
    assert any(line.startswith("updated:") for line in frontmatter_lines)
    assert body_lines == ["# Note", "Body line"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "heading", "payload", "expected_body"),
    [
        (
            "append",
            ["Section"],
            "Tail line",
            ["# Note", "# Section", "Alpha", "## Nested", "Beta", "Tail line"],
        ),
        (
            "prepend",
            ["Section", "Nested"],
            "Intro line",
            ["# Note", "# Section", "Alpha", "## Nested", "Intro line", "Beta"],
        ),
    ],
)
async def test_patch_appends_and_prepends_under_heading_update_body(
    operation: str,
    heading: list[str],
    payload: str,
    expected_body: list[str],
):
    initial_content = (
        "---\n"
        "id: note-1\n"
        "type: thought\n"
        "status: inbox\n"
        "created: 2026-09-10\n"
        "updated: 2026-09-10\n"
        "tags: []\n"
        "related: []\n"
        "---\n"
        "# Note\n"
        "# Section\n"
        "Alpha\n"
        "## Nested\n"
        "Beta\n"
    )
    obsidian_client = _InMemoryPatchClient(initial_content=initial_content)
    service = _build_service(obsidian_client=obsidian_client)

    if operation == "append":
        patch_operation = AppendUnderHeadingPatchOperation(
            kind="append_under_heading",
            heading=heading,
            content=payload,
        )
    else:
        patch_operation = PrependUnderHeadingPatchOperation(
            kind="prepend_under_heading",
            heading=heading,
            content=payload,
        )

    await service.patch(project_id="project-1", path="10 Notes/a.md", operation=patch_operation)

    _, body_lines = _split_frontmatter_and_body(obsidian_client.content)
    assert body_lines == expected_body


@pytest.mark.asyncio
async def test_patch_replaces_block_and_keeps_neighboring_blocks_unchanged():
    initial_content = (
        "---\n"
        "id: note-1\n"
        "type: thought\n"
        "status: inbox\n"
        "created: 2026-09-10\n"
        "updated: 2026-09-10\n"
        "tags: []\n"
        "related: []\n"
        "---\n"
        "# Note\n"
        "First block\n"
        "\n"
        "Target block\n"
        "\n"
        "Last block\n"
    )
    obsidian_client = _InMemoryPatchClient(initial_content=initial_content)
    service = _build_service(obsidian_client=obsidian_client)

    operation = ReplaceBlockPatchOperation(
        kind="replace_block",
        target="Target block",
        content="Replaced block",
    )
    await service.patch(project_id="project-1", path="10 Notes/a.md", operation=operation)

    _, body_lines = _split_frontmatter_and_body(obsidian_client.content)
    assert body_lines == [
        "# Note",
        "First block",
        "",
        "Replaced block",
        "",
        "Last block",
    ]
