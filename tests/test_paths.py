import pytest

from notes_facade.notes.paths import (
    InvalidNotePathError,
    PathOutsideProjectError,
    absolute_to_project_relative_path,
    build_note_path,
    sanitize_note_title,
)


def test_sanitize_note_title_removes_invalid_characters():
    assert sanitize_note_title("  Idea: about / facade?  ") == "Idea about facade"
    assert sanitize_note_title("...") == "Untitled"


def test_build_note_path_deduplicates_deterministically():
    existing_paths = [
        "00 Inbox/Idea.md",
        "00 Inbox/Idea 2.md",
    ]

    path = build_note_path(
        title="Idea",
        folder="00 Inbox",
        existing_paths=existing_paths,
    )

    assert path == "00 Inbox/Idea 3.md"


@pytest.mark.parametrize(
    "path",
    ["../escape.md", "a/../../b.md", "_system/x.md", ".obsidian/x.md"],
)
def test_invalid_paths_are_rejected(path):
    with pytest.raises(InvalidNotePathError):
        absolute_to_project_relative_path(absolute_path=path, project_folder="personal")


def test_absolute_to_project_relative_path_rejects_other_project():
    with pytest.raises(PathOutsideProjectError):
        absolute_to_project_relative_path(
            absolute_path="/vault/other/note.md",
            project_folder="personal",
        )


def test_absolute_unscoped_path_is_rejected_as_outside_project():
    with pytest.raises(PathOutsideProjectError):
        absolute_to_project_relative_path(
            absolute_path="/abs/path.md",
            project_folder="personal",
        )
