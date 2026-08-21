"""Interest preset tests."""

from app.modules.interests.presets import (
    INTEREST_PRESETS,
    PRESET_VERSION,
    initial_topic_values,
    interest_presets_payload,
)


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
