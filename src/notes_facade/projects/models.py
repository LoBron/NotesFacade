"""Project domain models."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

NonEmptyString = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class Project(BaseModel):
    """Configured project definition."""

    id: NonEmptyString
    name: NonEmptyString
    folder: NonEmptyString

    model_config = ConfigDict(extra="forbid")
