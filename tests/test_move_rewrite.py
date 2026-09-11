from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from notes_facade.notes.service import NotesService
from notes_facade.obsidian.errors import ObsidianUnavailableError
from notes_facade.obsidian.models import SearchResponse, SearchResultItem, VaultNoteResponse
from notes_facade.projects.models import Project

SOURCE_NOTE_CONTENT = (
    "---\n"
    "id: s\n"
    "type: thought\n"
    "status: inbox\n"
    "created: 2026-09-10\n"
    "updated: 2026-09-10\n"
    "tags: []\n"
    "related: []\n"
    "---\n"
    "Source"
)


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
async def test_move_note_rewrites_plain_and_aliased_links_in_order():
    source_path = "personal/10 Notes/Old.md"
    target_path = "personal/90 Archive/New.md"
    ref_path = "personal/10 Notes/Refs.md"
    call_order: list[str] = []
    patch_payloads: list[str] = []

    class RecordingClient:
        async def get_note(self, request):
            call_order.append(f"get:{request.path}")
            if request.path == source_path:
                return VaultNoteResponse(path=request.path, content=SOURCE_NOTE_CONTENT)
            if request.path == ref_path:
                return VaultNoteResponse(
                    path=request.path,
                    content=(
                        "---\n"
                        "id: r\n"
                        "type: thought\n"
                        "status: inbox\n"
                        "created: 2026-09-10\n"
                        "updated: 2026-09-10\n"
                        "tags: []\n"
                        "related: []\n"
                        "---\n"
                        "# Links\n"
                        "See [[Old]] and [[Old|alias]].\n"
                    ),
                )
            raise AssertionError(f"unexpected get_note path: {request.path}")

        async def put_note(self, request):
            call_order.append(f"put:{request.path}")
            assert request.path == target_path
            return None

        async def search_simple(self, request):
            call_order.append(f"search:{request.query}")
            return SearchResponse(
                results=[
                    SearchResultItem(filename=f"/vault/{source_path}", score=0.9),
                    SearchResultItem(filename=f"/vault/{ref_path}", score=0.8),
                ]
            )

        async def patch_note(self, request):
            call_order.append(f"patch:{request.path}")
            patch_payloads.append(request.body.content or "")
            return None

        async def delete_note(self, request):
            call_order.append(f"delete:{request.path}")
            assert request.path == source_path
            return None

    service = _build_service(obsidian_client=RecordingClient())

    result = await service.move_note(
        project_id="project-1",
        path="10 Notes/Old.md",
        new_path="90 Archive/New.md",
    )

    assert result.success is True
    assert result.original_deleted is True
    assert result.links_rewritten[0].path == "10 Notes/Refs.md"
    assert result.links_rewritten[0].replacements == 1
    assert patch_payloads == ["See [[New]] and [[New|alias]]."]
    assert call_order.index(f"put:{target_path}") < call_order.index(f"patch:{ref_path}")
    assert call_order[-1] == f"delete:{source_path}"


@pytest.mark.asyncio
async def test_move_note_skips_self_link_and_rewrites_other_notes():
    source_path = "personal/10 Notes/Old.md"
    target_path = "personal/90 Archive/New.md"
    self_path = target_path
    first_ref_path = "personal/10 Notes/Refs-A.md"
    second_ref_path = "personal/10 Notes/Refs-B.md"
    notes: dict[str, str] = {
        source_path: (
            "---\n"
            "id: s\n"
            "type: thought\n"
            "status: inbox\n"
            "created: 2026-09-10\n"
            "updated: 2026-09-10\n"
            "tags: []\n"
            "related: []\n"
            "---\n"
            "# Links\n"
            "Keep [[Old]] and [[Old|alias]].\n"
        ),
        first_ref_path: (
            "---\n"
            "id: a\n"
            "type: thought\n"
            "status: inbox\n"
            "created: 2026-09-10\n"
            "updated: 2026-09-10\n"
            "tags: []\n"
            "related: []\n"
            "---\n"
            "# Links\n"
            "First [[Old]] link.\n"
        ),
        second_ref_path: (
            "---\n"
            "id: b\n"
            "type: thought\n"
            "status: inbox\n"
            "created: 2026-09-10\n"
            "updated: 2026-09-10\n"
            "tags: []\n"
            "related: []\n"
            "---\n"
            "# Links\n"
            "Second [[Old#Section|alias]] link.\n"
        ),
    }

    class RecordingClient:
        async def get_note(self, request):
            return VaultNoteResponse(path=request.path, content=notes[request.path])

        async def put_note(self, request):
            notes[request.path] = request.content
            return None

        async def search_simple(self, request):
            return SearchResponse(
                results=[
                    SearchResultItem(filename=f"/vault/{source_path}", score=0.9),
                    SearchResultItem(filename=f"/vault/{self_path}", score=0.85),
                    SearchResultItem(filename=f"/vault/{first_ref_path}", score=0.8),
                    SearchResultItem(filename=f"/vault/{second_ref_path}", score=0.75),
                ]
            )

        async def patch_note(self, request):
            notes[request.path] = request.body.content or ""
            return None

        async def delete_note(self, request):
            notes.pop(request.path, None)
            return None

    service = _build_service(obsidian_client=RecordingClient())

    result = await service.move_note(
        project_id="project-1",
        path="10 Notes/Old.md",
        new_path="90 Archive/New.md",
    )

    assert result.success is True
    assert {item.path for item in result.links_rewritten} == {
        "10 Notes/Refs-A.md",
        "10 Notes/Refs-B.md",
    }
    assert self_path in notes
    assert "[[Old]]" in notes[self_path]
    assert "[[New]]" in notes[first_ref_path]
    assert "[[New#Section|alias]]" in notes[second_ref_path]
    assert source_path not in notes


@pytest.mark.asyncio
async def test_move_note_returns_partial_report_on_rewrite_failure():
    source_path = "personal/10 Notes/Old.md"
    target_path = "personal/90 Archive/New.md"
    ref_path = "personal/10 Notes/Refs.md"
    patch_attempts = 0
    delete_called = False

    class PartiallyFailingClient:
        async def get_note(self, request):
            if request.path == source_path:
                return VaultNoteResponse(path=request.path, content=SOURCE_NOTE_CONTENT)
            return VaultNoteResponse(
                path=request.path,
                content=(
                    "---\n"
                    "id: r\n"
                    "type: thought\n"
                    "status: inbox\n"
                    "created: 2026-09-10\n"
                    "updated: 2026-09-10\n"
                    "tags: []\n"
                    "related: []\n"
                    "---\n"
                    "# Links\n"
                    "See [[Old]]\n"
                    "\n"
                    "And [[Old|alias]]\n"
                ),
            )

        async def put_note(self, request):
            assert request.path == target_path
            return None

        async def search_simple(self, request):
            return SearchResponse(results=[SearchResultItem(filename=f"/vault/{ref_path}")])

        async def patch_note(self, request):
            nonlocal patch_attempts
            patch_attempts += 1
            if patch_attempts == 1:
                return None
            raise ObsidianUnavailableError("patch failed on second block")

        async def delete_note(self, request):
            nonlocal delete_called
            delete_called = True
            raise AssertionError("delete should not be called on rewrite failure")

    service = _build_service(obsidian_client=PartiallyFailingClient())

    result = await service.move_note(
        project_id="project-1",
        path="10 Notes/Old.md",
        new_path="90 Archive/New.md",
    )

    assert result.success is False
    assert result.copied is True
    assert result.original_deleted is False
    assert result.failed_step == "rewrite_links"
    assert patch_attempts == 2
    assert delete_called is False
    assert len(result.links_rewritten) == 1
    assert result.links_rewritten[0].path == "10 Notes/Refs.md"
    assert result.links_rewritten[0].replacements == 1
    assert "patch failed on second block" in (result.error or "")
