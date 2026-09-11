from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from notes_facade.notes.service import (
    AppendUnderHeadingPatchOperation,
    InvalidPatchOperationError,
    NotesService,
    RemoveFieldPatchOperation,
    SetFieldPatchOperation,
)
from notes_facade.obsidian.models import PatchTargetTypes
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
    assert first_call.body.value == "active"

    second_call = obsidian_client.patch_note.await_args_list[1].args[0]
    assert second_call.body.target == "updated"
    assert second_call.body.target_type == PatchTargetTypes.FRONTMATTER
    assert second_call.body.operation == "replace"
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
    assert first_call.body.target == ["Links"]
    assert first_call.body.content == "- [[Ref]]"
