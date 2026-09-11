"""Read-only project notes validation utilities."""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from notes_facade.notes.paths import NotePathError, normalize_project_relative_path
from notes_facade.notes.schema import FrontmatterValidationError, parse_note_document

MARKDOWN_EXTENSION = ".md"
WIKILINK_PATTERN = re.compile(
    r"!?"
    r"\[\["
    r"(?P<target>[^\]|#]+)"
    r"(?:#[^\]|]+)?"
    r"(?:\|[^\]]+)?"
    r"\]\]"
)
ORPHAN_NOTE_REASON = "Note has no incoming and outgoing wikilinks"
INVALID_LINK_MESSAGE = "Broken wikilink target '%s': %s"
UNRESOLVED_LINK_MESSAGE = "Broken wikilink target: %s"


class ValidateIssue(BaseModel):
    """Single validation issue item."""

    path: str
    reason: str

    model_config = ConfigDict(extra="forbid")


class ValidationCounters(BaseModel):
    """Summary counters for validate report."""

    notes: int = Field(ge=0)
    links: int = Field(ge=0)
    broken_links: int = Field(ge=0)
    orphan_notes: int = Field(ge=0)
    errors: int = Field(ge=0)
    warnings: int = Field(ge=0)
    info: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class ValidateReport(BaseModel):
    """Validation report grouped by severity."""

    errors: list[ValidateIssue]
    warnings: list[ValidateIssue]
    info: list[ValidateIssue]
    counters: ValidationCounters

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class _NoteSnapshot:
    path: str
    note_id: str | None
    links: set[_DiscoveredLink]


@dataclass(frozen=True)
class _DiscoveredLink:
    raw_target: str
    normalized_target: str | None
    invalid_reason: str | None


class ProjectNotesValidator:
    """Validates note files inside a single project read-only scope."""

    def __init__(self, *, vault_ro_path: Path) -> None:
        self._vault_ro_path = vault_ro_path

    def validate(self, *, project_folder: str) -> ValidateReport:
        """Validate project notes and return structured diagnostics."""
        errors: list[ValidateIssue] = []
        warnings: list[ValidateIssue] = []
        info: list[ValidateIssue] = []
        notes: list[_NoteSnapshot] = []

        project_root = self._resolve_project_root(project_folder=project_folder, errors=errors)
        if project_root is None:
            return self._build_report(
                errors=errors,
                warnings=warnings,
                info=info,
                notes=[],
                links=0,
            )
        if not project_root.exists():
            return self._build_report(
                errors=errors,
                warnings=warnings,
                info=info,
                notes=[],
                links=0,
            )
        if not project_root.is_dir():
            errors.append(
                ValidateIssue(path="", reason="Configured project folder is not a directory")
            )
            return self._build_report(
                errors=errors,
                warnings=warnings,
                info=info,
                notes=[],
                links=0,
            )

        project_root_resolved = project_root.resolve(strict=True)
        notes, links = self._scan_markdown_files(
            project_root=project_root,
            project_root_resolved=project_root_resolved,
            errors=errors,
        )
        self._collect_duplicate_id_errors(notes=notes, errors=errors)
        self._collect_wikilink_issues_and_orphans(
            notes=notes,
            warnings=warnings,
            info=info,
        )
        return self._build_report(
            errors=errors,
            warnings=warnings,
            info=info,
            notes=notes,
            links=links,
        )

    def _resolve_project_root(
        self,
        *,
        project_folder: str,
        errors: list[ValidateIssue],
    ) -> Path | None:
        try:
            normalized_folder = normalize_project_relative_path(project_folder)
        except NotePathError:
            errors.append(ValidateIssue(path="", reason="Configured project folder is invalid"))
            return None

        vault_root = self._vault_ro_path.resolve(strict=False)
        project_root = (vault_root / normalized_folder).resolve(strict=False)
        if not project_root.is_relative_to(vault_root):
            errors.append(
                ValidateIssue(path="", reason="Configured project folder escapes VAULT_RO_PATH")
            )
            return None
        return project_root

    def _scan_markdown_files(
        self,
        *,
        project_root: Path,
        project_root_resolved: Path,
        errors: list[ValidateIssue],
    ) -> tuple[list[_NoteSnapshot], int]:
        notes: list[_NoteSnapshot] = []
        links = 0

        for current_dir, dirnames, filenames in os.walk(
            project_root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current_dir)
            current_resolved = current_path.resolve(strict=False)
            if not current_resolved.is_relative_to(project_root_resolved):
                dirnames[:] = []
                continue

            dirnames[:] = self._filter_safe_directories(
                parent=current_path,
                names=dirnames,
                project_root_resolved=project_root_resolved,
            )
            for filename in filenames:
                if not filename.endswith(MARKDOWN_EXTENSION):
                    continue
                if self._is_service_path_segment(filename):
                    continue

                file_path = current_path / filename
                resolved_file_path = file_path.resolve(strict=False)
                if not resolved_file_path.is_relative_to(project_root_resolved):
                    continue

                relative_path = file_path.relative_to(project_root).as_posix()
                try:
                    file_content = file_path.read_text(encoding="utf-8")
                except OSError as error:
                    errors.append(
                        ValidateIssue(
                            path=relative_path,
                            reason=f"Cannot read file: {error.__class__.__name__}",
                        )
                    )
                    continue

                body = self._extract_raw_note_body(content=file_content)
                related_items = self._extract_related_items(content=file_content)
                note_id: str | None = None
                try:
                    frontmatter_data, body = parse_note_document(file_content)
                    note_id = frontmatter_data.id
                    related_items = frontmatter_data.related
                except FrontmatterValidationError as error:
                    errors.append(
                        ValidateIssue(
                            path=relative_path,
                            reason=self._build_frontmatter_error_message(error),
                        )
                    )

                note_links = self._extract_links(body=body, related=related_items)
                links += len(note_links)
                notes.append(
                    _NoteSnapshot(
                        path=relative_path,
                        note_id=note_id,
                        links=note_links,
                    )
                )
        return notes, links

    def _filter_safe_directories(
        self,
        *,
        parent: Path,
        names: list[str],
        project_root_resolved: Path,
    ) -> list[str]:
        safe_dirs: list[str] = []
        for name in names:
            if self._is_service_path_segment(name):
                continue
            candidate_path = parent / name
            resolved_candidate = candidate_path.resolve(strict=False)
            if not resolved_candidate.is_relative_to(project_root_resolved):
                continue
            safe_dirs.append(name)
        return safe_dirs

    def _collect_duplicate_id_errors(
        self,
        *,
        notes: list[_NoteSnapshot],
        errors: list[ValidateIssue],
    ) -> None:
        seen_ids: dict[str, str] = {}
        for note in notes:
            if note.note_id is None:
                continue
            original_path = seen_ids.get(note.note_id)
            if original_path is None:
                seen_ids[note.note_id] = note.path
                continue
            errors.append(
                ValidateIssue(
                    path=note.path,
                    reason=f"Duplicate id '{note.note_id}' already used in {original_path}",
                )
            )

    def _collect_wikilink_issues_and_orphans(
        self,
        *,
        notes: list[_NoteSnapshot],
        warnings: list[ValidateIssue],
        info: list[ValidateIssue],
    ) -> None:
        if not notes:
            return

        by_relative_target: dict[str, set[str]] = {}
        by_name_target: dict[str, set[str]] = {}
        incoming_counts: dict[str, int] = {note.path: 0 for note in notes}
        outgoing_counts: dict[str, int] = {note.path: len(note.links) for note in notes}

        for note in notes:
            relative_no_ext = note.path[: -len(MARKDOWN_EXTENSION)]
            by_relative_target.setdefault(relative_no_ext, set()).add(note.path)
            by_name_target.setdefault(PurePosixPath(relative_no_ext).name, set()).add(note.path)

        for note in notes:
            for link in note.links:
                if link.invalid_reason is not None:
                    warnings.append(
                        ValidateIssue(
                            path=note.path,
                            reason=INVALID_LINK_MESSAGE % (link.raw_target, link.invalid_reason),
                        )
                    )
                    continue
                resolved_targets = self._resolve_link_target(
                    target=link.normalized_target,
                    by_relative_target=by_relative_target,
                    by_name_target=by_name_target,
                )
                if not resolved_targets:
                    warnings.append(
                        ValidateIssue(
                            path=note.path,
                            reason=UNRESOLVED_LINK_MESSAGE % link.raw_target,
                        )
                    )
                    continue
                for resolved_target in resolved_targets:
                    incoming_counts[resolved_target] += 1

        for note in notes:
            if incoming_counts[note.path] == 0 and outgoing_counts[note.path] == 0:
                info.append(ValidateIssue(path=note.path, reason=ORPHAN_NOTE_REASON))

    def _resolve_link_target(
        self,
        *,
        target: str | None,
        by_relative_target: dict[str, set[str]],
        by_name_target: dict[str, set[str]],
    ) -> set[str]:
        if target is None:
            return set()

        resolved_targets: set[str] = set()
        resolved_targets.update(by_relative_target.get(target, set()))
        if "/" in target:
            return resolved_targets
        target_name = PurePosixPath(target).name
        resolved_targets.update(by_name_target.get(target_name, set()))
        return resolved_targets

    def _extract_links(self, *, body: str, related: list[str]) -> set[_DiscoveredLink]:
        result: set[_DiscoveredLink] = set()
        for match in WIKILINK_PATTERN.finditer(body):
            raw_target = match.group("target")
            result.add(self._build_discovered_link(target=raw_target))
        for related_item in related:
            related_links = self._parse_related_entry(related_item)
            if related_links:
                result.update(related_links)
                continue
            result.add(self._build_discovered_link(target=related_item))
        return result

    def _parse_related_entry(self, related_item: str) -> set[_DiscoveredLink]:
        links: set[_DiscoveredLink] = set()
        for match in WIKILINK_PATTERN.finditer(related_item):
            raw_target = match.group("target")
            links.add(self._build_discovered_link(target=raw_target))
        return links

    def _build_discovered_link(self, *, target: str) -> _DiscoveredLink:
        normalized_target, invalid_reason = self._normalize_wikilink_target(target=target)
        return _DiscoveredLink(
            raw_target=target.strip(),
            normalized_target=normalized_target,
            invalid_reason=invalid_reason,
        )

    def _normalize_wikilink_target(self, *, target: str) -> tuple[str | None, str | None]:
        normalized = target.replace("\\", "/").strip()
        if not normalized:
            return None, "target is empty"
        if normalized.startswith("/"):
            return None, "absolute paths are forbidden"

        source_parts = PurePosixPath(normalized).parts
        if any(part == ".." for part in source_parts):
            return None, "path traversal is forbidden"

        normalized_path = posixpath.normpath(normalized)
        if normalized_path in {"", ".", ".."}:
            return None, "target is invalid"
        if normalized_path.startswith("../"):
            return None, "path traversal is forbidden"

        parts = PurePosixPath(normalized_path).parts
        if any(part == ".." for part in parts):
            return None, "path traversal is forbidden"
        for part in parts:
            if part == "_system":
                return None, "target points to _system"
            if part.startswith("."):
                return None, "target points to hidden path"
        if normalized_path.endswith(MARKDOWN_EXTENSION):
            return normalized_path[: -len(MARKDOWN_EXTENSION)], None
        return normalized_path, None

    def _build_frontmatter_error_message(self, error: FrontmatterValidationError) -> str:
        messages: list[str] = []
        for item in error.errors:
            location = ".".join(str(chunk) for chunk in item.get("loc", ()))
            message = str(item.get("msg", "Invalid value"))
            if location:
                messages.append(f"{location}: {message}")
            else:
                messages.append(message)
        if not messages:
            return "Invalid frontmatter schema"
        return f"Invalid frontmatter schema: {'; '.join(messages)}"

    def _build_report(
        self,
        *,
        errors: list[ValidateIssue],
        warnings: list[ValidateIssue],
        info: list[ValidateIssue],
        notes: list[_NoteSnapshot],
        links: int,
    ) -> ValidateReport:
        return ValidateReport(
            errors=errors,
            warnings=warnings,
            info=info,
            counters=ValidationCounters(
                notes=len(notes),
                links=links,
                broken_links=len(warnings),
                orphan_notes=len(info),
                errors=len(errors),
                warnings=len(warnings),
                info=len(info),
            ),
        )

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

    def _extract_related_items(self, *, content: str) -> list[str]:
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return []

        metadata_lines: list[str] = []
        for index in range(1, len(lines)):
            line = lines[index]
            if line.strip() == "---":
                break
            metadata_lines.append(line)
        if not metadata_lines:
            return []

        related_items: list[str] = []
        in_related_block = False
        for line in metadata_lines:
            stripped = line.strip()
            if stripped.startswith("related:"):
                in_related_block = True
                inline_value = stripped[len("related:") :].strip()
                if inline_value and inline_value != "[]":
                    related_items.append(inline_value)
                continue
            if not in_related_block:
                continue
            if not stripped:
                continue
            if line.startswith((" ", "\t")):
                item = stripped.lstrip("-").strip()
                if item:
                    related_items.append(item)
                continue
            break

        return related_items

    def _is_service_path_segment(self, segment: str) -> bool:
        normalized = segment.strip()
        return normalized == "_system" or normalized.startswith(".")
