"""Analysis prompt tests."""

from app.modules.analysis.prompts import (
    DEEP_ANALYSIS_SYSTEM_PROMPT,
    PROMPT_TEMPLATES,
    SCREENING_SYSTEM_PROMPT,
    prompt_catalog_payload,
)


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
