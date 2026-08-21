"""Direction, organization, analysis, and prompt catalog tests."""

from __future__ import annotations

import pytest
from app.models import Topic
from app.services.analysis.analyzer import ANALYSIS_BUDGETS, analysis_budget
from app.services.analysis.prompts import (
    DEEP_ANALYSIS_SYSTEM_PROMPT,
    PROMPT_TEMPLATES,
    SCREENING_SYSTEM_PROMPT,
    prompt_catalog_payload,
)
from app.services.directions.catalog import (
    CATALOG_VERSION,
    DIRECTION_CATALOG,
    direction_catalog_payload,
)
from app.services.directions.matching import canonical_requested_direction_names
from app.services.organizations import watched_organizations


def test_direction_catalog_is_versioned_searchable_and_collision_free() -> None:
    payload = direction_catalog_payload()
    assert payload["version"] == CATALOG_VERSION
    assert payload["count"] == len(DIRECTION_CATALOG)
    assert payload["count"] >= 30
    assert len(payload["groups"]) >= 7
    identities: dict[str, str] = {}
    for item in payload["items"]:
        assert item["name"].strip()
        assert item["description"].strip()
        assert item["keywords"]
        assert item["arxiv_categories"]
        for label in (item["name"], *item["aliases"]):
            key = "".join(label.casefold().replace("_", "-").split()).replace("-", "")
            assert key not in identities, f"{label} conflicts with {identities.get(key)}"
            identities[key] = label
    by_slug = {item["slug"]: item for item in payload["items"]}
    for item in payload["items"]:
        if item["level"] == "specialty":
            assert item["parent_slug"] in by_slug
            assert by_slug[item["parent_slug"]]["level"] == "main"
    assert "大语言模型与基础模型" in {item["name"] for item in payload["items"]}
    assert "视觉-语言-动作模型" in {item["name"] for item in payload["items"]}
    assert "AI for Science 与科学发现" in {item["name"] for item in payload["items"]}


def test_watched_organization_normalization_avoids_name_collisions() -> None:
    assert watched_organizations(["Nvidia Corporation", "Stanford University"]) == ["NVIDIA"]
    assert watched_organizations(["Meta-analysis Research Institute"]) == []
    assert watched_organizations(["OpenAIR Science Center"]) == []
    assert watched_organizations(["Google LLC, Mountain View"]) == ["Google Research"]
    assert watched_organizations(["Allen Institute for AI"]) == ["Allen Institute for AI (AI2)"]


def test_direction_alias_is_canonicalized_without_creating_a_new_label() -> None:
    topics = [
        Topic(
            name="视觉-语言-动作模型",
            aliases_json=["VLA", "Vision-Language-Action"],
            enabled=True,
        )
    ]
    names = canonical_requested_direction_names(
        ["VLA", "Vision-Language-Action"],
        topics,
        relevant=True,
    )
    assert names == ["视觉-语言-动作模型"]


def test_prompt_catalog_is_versioned_and_cross_domain() -> None:
    catalog = prompt_catalog_payload()
    assert catalog["version"]
    assert catalog["default_template_id"] == "balanced-review"
    assert len(PROMPT_TEMPLATES) == 5
    assert len({item.id for item in PROMPT_TEMPLATES}) == len(PROMPT_TEMPLATES)
    assert {item.id for item in PROMPT_TEMPLATES} >= {
        "balanced-review",
        "evidence-audit",
        "innovation-scout",
        "reproduction-engineering",
        "application-transfer",
    }
    assert "论文粗排助手" in SCREENING_SYSTEM_PROMPT
    assert "跨领域 AI" in DEEP_ANALYSIS_SYSTEM_PROMPT
    assert "screening_prompt" not in catalog["templates"][0]


def test_analysis_budget_modes() -> None:
    assert ANALYSIS_BUDGETS["economy"].max_fulltext_chars == 16_000
    assert ANALYSIS_BUDGETS["balanced"].max_pdf_pages == 25
    assert ANALYSIS_BUDGETS["deep"].deep_output_tokens == 5_000
    with pytest.raises(KeyError):
        analysis_budget("unknown")  # type: ignore[arg-type]
