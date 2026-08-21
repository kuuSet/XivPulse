"""Interest presets, organization, analysis, and prompt tests."""

from __future__ import annotations

import pytest
from app.modules.interests.presets import (
    INTEREST_PRESETS,
    PRESET_VERSION,
    initial_topic_values,
    interest_presets_payload,
)
from app.services.analysis.analyzer import ANALYSIS_BUDGETS, analysis_budget
from app.services.analysis.prompts import (
    DEEP_ANALYSIS_SYSTEM_PROMPT,
    PROMPT_TEMPLATES,
    SCREENING_SYSTEM_PROMPT,
    prompt_catalog_payload,
)
from app.services.organizations import watched_organizations


def test_interest_presets_are_versioned_searchable_and_collision_free() -> None:
    payload = interest_presets_payload()
    assert payload["version"] == PRESET_VERSION
    assert len(payload["items"]) == len(INTEREST_PRESETS)
    assert len(payload["items"]) >= 30
    identities: dict[str, str] = {}
    for item in payload["items"]:
        assert item["name"].strip()
        assert item["description"].strip()
        assert item["arxiv_categories"]
        for label in (item["name"], *item["aliases"]):
            key = "".join(label.casefold().replace("_", "-").split()).replace("-", "")
            assert key not in identities, f"{label} conflicts with {identities.get(key)}"
            identities[key] = label
    defaults = initial_topic_values()
    assert len(defaults) == 6
    assert all(value["description"].isascii() for value in defaults)


def test_watched_organization_normalization_avoids_name_collisions() -> None:
    assert watched_organizations(["Nvidia Corporation", "Stanford University"]) == ["NVIDIA"]
    assert watched_organizations(["Meta-analysis Research Institute"]) == []
    assert watched_organizations(["OpenAIR Science Center"]) == []
    assert watched_organizations(["Google LLC, Mountain View"]) == ["Google Research"]
    assert watched_organizations(["Allen Institute for AI"]) == ["Allen Institute for AI (AI2)"]


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
