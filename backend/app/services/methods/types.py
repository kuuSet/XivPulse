"""Validated user-defined method labels."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MethodLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]


class CustomMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: MethodLabel
    aliases: tuple[MethodLabel, ...] = Field(default=(), max_length=20)


__all__ = ["CustomMethod"]
