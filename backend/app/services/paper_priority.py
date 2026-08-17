"""Priority policy for papers matching explicitly watched people or organizations."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from ..models import Paper


def _author_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def _organization_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"[_\W]+", " ", normalized).strip()


class PaperPriorityPolicy:
    def __init__(
        self,
        *,
        watched_authors: Sequence[str],
        watched_organizations: Sequence[str],
        prioritize_authors: bool,
        prioritize_organizations: bool,
    ) -> None:
        self.authors = frozenset(filter(None, map(_author_key, watched_authors)))
        self.organizations = tuple(
            dict.fromkeys(filter(None, map(_organization_key, watched_organizations)))
        )
        self.prioritize_authors = prioritize_authors
        self.prioritize_organizations = prioritize_organizations

    def rank(self, paper: Paper) -> int:
        author_match = self.prioritize_authors and any(
            _author_key(author) in self.authors for author in paper.authors
        )
        organization_match = self.prioritize_organizations and any(
            f" {organization} " in f" {_organization_key(affiliation)} "
            for organization in self.organizations
            for affiliation in paper.affiliations
        )
        return int(author_match) + int(organization_match)


__all__ = ["PaperPriorityPolicy"]
