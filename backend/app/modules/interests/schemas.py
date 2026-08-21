"""Validated followed-interest input."""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TopicWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    aliases: list[str] = Field(default_factory=list)
    arxiv_categories: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("name", "description", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("aliases", "arxiv_categories")
    @classmethod
    def clean_list(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result


class InterestsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[TopicWrite]
    watched_authors: list[str] = Field(default_factory=list)
    watched_organizations: list[str] = Field(default_factory=list)

    @field_validator("watched_authors", "watched_organizations")
    @classmethod
    def clean_names(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @model_validator(mode="after")
    def validate_topic_names(self) -> InterestsUpdate:
        if not any(topic.enabled for topic in self.topics):
            raise ValueError("至少需要启用一个关注标签")
        identities: dict[str, str] = {}
        for topic in self.topics:
            for raw in (topic.name, *topic.aliases):
                normalized = unicodedata.normalize("NFKC", raw).strip().casefold()
                key = re.sub(r"[\s_-]+", "", normalized)
                previous = identities.get(key)
                if previous is not None:
                    raise ValueError(f"标签名称或别名重复：{raw} 与 {previous}")
                identities[key] = raw
        return self


__all__ = ["InterestsUpdate", "TopicWrite"]
