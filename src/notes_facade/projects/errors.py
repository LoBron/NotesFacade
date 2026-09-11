"""Project-related exceptions."""

UNKNOWN_PROJECT_ERROR_MESSAGE = "Unknown or missing project_id"


class NotesFacadeError(Exception):
    """Base exception for Notes Facade domain errors."""


class UnknownProjectError(NotesFacadeError):
    """Raised when provided project_id does not match configured projects."""

    def __init__(self) -> None:
        super().__init__(UNKNOWN_PROJECT_ERROR_MESSAGE)
