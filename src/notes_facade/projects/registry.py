"""Project registry based on JSON configuration."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from notes_facade.projects.errors import UnknownProjectError
from notes_facade.projects.models import Project

LOGGER = logging.getLogger(__name__)
_TRUNCATED_ID_SUFFIX = "..."
_TRUNCATED_ID_PREFIX_LENGTH = 8


class _ProjectsConfig(BaseModel):
    projects: list[Project]

    model_config = ConfigDict(extra="forbid")


class ProjectRegistry:
    """Loads and resolves projects from configuration."""

    def __init__(self, config_path: str | Path) -> None:
        self._config_path = Path(config_path)
        self._projects_by_id = self._load_projects_by_id()

    def resolve(self, project_id: str) -> Project:
        """Resolve project by opaque string project_id."""
        if not project_id:
            self._log_unknown_project_id(project_id=project_id)
            raise UnknownProjectError()

        project = self._projects_by_id.get(project_id)
        if project is None:
            self._log_unknown_project_id(project_id=project_id)
            raise UnknownProjectError()
        return project

    def _load_projects_by_id(self) -> dict[str, Project]:
        try:
            raw_config = self._config_path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError(f"Cannot read projects config file: {self._config_path}") from error

        try:
            parsed_config = json.loads(raw_config)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON in projects config file: {self._config_path}"
            ) from error

        try:
            projects_config = _ProjectsConfig.model_validate(parsed_config)
        except ValidationError as error:
            raise ValueError(
                f"Invalid projects config schema in file: {self._config_path}"
            ) from error

        projects_by_id: dict[str, Project] = {}
        folders: set[str] = set()
        for project in projects_config.projects:
            if project.id in projects_by_id:
                raise ValueError(f"Duplicate project id in config: {project.id}")
            if project.folder in folders:
                raise ValueError(f"Duplicate project folder in config: {project.folder}")
            projects_by_id[project.id] = project
            folders.add(project.folder)

        return projects_by_id

    def _log_unknown_project_id(self, project_id: str) -> None:
        LOGGER.warning(
            "Unknown project_id resolution attempt: %s",
            self._truncate_project_id(project_id=project_id),
        )

    def _truncate_project_id(self, project_id: str) -> str:
        if not project_id:
            return "<empty>"
        if len(project_id) <= _TRUNCATED_ID_PREFIX_LENGTH:
            return project_id
        return project_id[:_TRUNCATED_ID_PREFIX_LENGTH] + _TRUNCATED_ID_SUFFIX
