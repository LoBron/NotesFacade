from datetime import date

import pytest

from notes_facade.notes.schema import (
    Frontmatter,
    FrontmatterValidationError,
    NoteStatuses,
    NoteTypes,
    parse_note_document,
    render_note_document,
)


def test_parse_note_document_valid_frontmatter():
    content = """---
id: note-1
type: thought
status: inbox
created: 2026-09-10
updated: 2026-09-10
tags:
  - tag-a
related:
  - "[[Other]]"
---
Body text.
"""

    frontmatter_data, body = parse_note_document(content)

    assert frontmatter_data.id == "note-1"
    assert frontmatter_data.type == NoteTypes.THOUGHT
    assert frontmatter_data.status == NoteStatuses.INBOX
    assert body.strip() == "Body text."


@pytest.mark.parametrize(
    "content",
    [
        "Body without frontmatter",
        """---
id: note-1
type: thought
status: inbox
created: 2026-09-10
updated: 2026-09-10
unknown: x
---
Body
""",
    ],
)
def test_parse_note_document_invalid_frontmatter(content):
    with pytest.raises(FrontmatterValidationError, match="Invalid note frontmatter"):
        parse_note_document(content)


def test_render_note_document_serializes_schema_fields():
    frontmatter_data = Frontmatter(
        id="note-2",
        type=NoteTypes.REFERENCE,
        status=NoteStatuses.ACTIVE,
        created=date(2026, 9, 10),
        updated=date(2026, 9, 11),
        tags=["python"],
        related=["[[Another]]"],
    )

    content = render_note_document(frontmatter_data=frontmatter_data, body="Some body")
    reparsed, body = parse_note_document(content)

    assert reparsed == frontmatter_data
    assert body == "Some body"
