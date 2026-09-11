import json

import pytest

from notes_facade.projects.errors import UNKNOWN_PROJECT_ERROR_MESSAGE, UnknownProjectError
from notes_facade.projects.registry import ProjectRegistry


def _write_projects_config(tmp_path, projects: list[dict[str, str]]):
    config_path = tmp_path / "projects.json"
    config_path.write_text(json.dumps({"projects": projects}), encoding="utf-8")
    return config_path


def test_registry_resolves_project_by_id(tmp_path):
    config_path = _write_projects_config(
        tmp_path,
        [{"id": "project-1", "name": "Personal", "folder": "personal"}],
    )
    registry = ProjectRegistry(config_path=config_path)

    project = registry.resolve("project-1")

    assert project.id == "project-1"
    assert project.folder == "personal"


@pytest.mark.parametrize("project_id", ["", "missing-id"])
def test_registry_raises_unified_error_for_unknown_project(project_id, tmp_path):
    config_path = _write_projects_config(
        tmp_path,
        [{"id": "project-1", "name": "Personal", "folder": "personal"}],
    )
    registry = ProjectRegistry(config_path=config_path)

    with pytest.raises(UnknownProjectError, match=UNKNOWN_PROJECT_ERROR_MESSAGE):
        registry.resolve(project_id)


def test_registry_fails_on_duplicate_project_id(tmp_path):
    config_path = _write_projects_config(
        tmp_path,
        [
            {"id": "project-1", "name": "Personal", "folder": "personal"},
            {"id": "project-1", "name": "Work", "folder": "work"},
        ],
    )

    with pytest.raises(ValueError, match="Duplicate project id"):
        ProjectRegistry(config_path=config_path)


def test_registry_fails_on_duplicate_project_folder(tmp_path):
    config_path = _write_projects_config(
        tmp_path,
        [
            {"id": "project-1", "name": "Personal", "folder": "shared"},
            {"id": "project-2", "name": "Work", "folder": "shared"},
        ],
    )

    with pytest.raises(ValueError, match="Duplicate project folder"):
        ProjectRegistry(config_path=config_path)
