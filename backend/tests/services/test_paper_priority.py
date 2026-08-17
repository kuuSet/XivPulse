"""Watched author and organization priority policy tests."""

from app.models import Paper
from app.services.paper_priority import PaperPriorityPolicy


def paper(*, authors: list[str], affiliations: list[str]) -> Paper:
    return Paper(
        arxiv_id="2608.12345",
        title="Test paper",
        authors_json=authors,
        affiliations_json=affiliations,
    )


def test_priority_is_disabled_by_default() -> None:
    policy = PaperPriorityPolicy(
        watched_authors=["Jane Doe"],
        watched_organizations=["NVIDIA"],
        prioritize_authors=False,
        prioritize_organizations=False,
    )

    assert policy.rank(paper(authors=["Jane Doe"], affiliations=["NVIDIA Research"])) == 0


def test_author_priority_requires_an_exact_normalized_name() -> None:
    policy = PaperPriorityPolicy(
        watched_authors=["  JANE DOE  "],
        watched_organizations=[],
        prioritize_authors=True,
        prioritize_organizations=False,
    )

    assert policy.rank(paper(authors=["Jane Doe"], affiliations=[])) == 1
    assert policy.rank(paper(authors=["Jane Doe-Smith"], affiliations=[])) == 0


def test_organization_priority_matches_a_complete_phrase() -> None:
    policy = PaperPriorityPolicy(
        watched_authors=[],
        watched_organizations=["NVIDIA"],
        prioritize_authors=False,
        prioritize_organizations=True,
    )

    assert policy.rank(paper(authors=[], affiliations=["NVIDIA Research"])) == 1
    assert policy.rank(paper(authors=[], affiliations=["NVIDIALabs"])) == 0


def test_author_and_organization_matches_have_additive_priority() -> None:
    policy = PaperPriorityPolicy(
        watched_authors=["Jane Doe"],
        watched_organizations=["NVIDIA"],
        prioritize_authors=True,
        prioritize_organizations=True,
    )

    both = paper(authors=["Jane Doe"], affiliations=["NVIDIA Research"])
    author_only = paper(authors=["Jane Doe"], affiliations=["Other Lab"])
    neither = paper(authors=["John Doe"], affiliations=["Other Lab"])

    assert [policy.rank(value) for value in (both, author_only, neither)] == [2, 1, 0]
