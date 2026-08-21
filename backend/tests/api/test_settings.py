"""Settings HTTP contract tests."""

from fastapi.testclient import TestClient


def test_settings_expose_only_llm_pipeline_controls(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["prefilter_batch_size"] == 30
    assert "embedding" not in payload
    assert "semantic_recall_mode" not in payload
    assert "semantic_recall_presets" not in payload
    assert set(payload["providers"]) == {"prefilter", "screening", "deep", "synthesis"}
    assert payload["providers"]["screening"]["model_source"] == "api"
    assert payload["providers"]["deep"]["codex_model"] == "gpt-5.6-sol"
    assert payload["providers"]["deep"]["codex_reasoning_effort"] == "medium"
    assert [item["value"] for item in payload["codex_model_options"]] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]

    updated = client.put(
        "/api/settings",
        json={
            "prefilter_batch_size": 40,
            "providers": {
                "screening": {
                    "model_source": "codex",
                    "codex_model": "gpt-5.6-terra",
                    "codex_reasoning_effort": "high",
                }
            },
        },
    )
    assert updated.status_code == 200
    assert updated.json()["prefilter_batch_size"] == 40
    assert updated.json()["providers"]["screening"]["model_source"] == "codex"
    assert updated.json()["providers"]["screening"]["codex_model"] == "gpt-5.6-terra"
    assert updated.json()["providers"]["screening"]["codex_reasoning_effort"] == "high"
    assert updated.json()["providers"]["deep"]["model_source"] == "api"
    assert (
        client.put(
            "/api/settings",
            json={"providers": {"deep": {"codex_model": "unknown"}}},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            json={"providers": {"deep": {"codex_reasoning_effort": "ultra"}}},
        ).status_code
        == 422
    )
    assert client.get("/api/prompts/catalog").json()["fixed_prompts"]["prefilter"]
    invalid_timezone = client.put(
        "/api/settings", json={"schedule_timezone": "Mars/Olympus"}
    )
    assert invalid_timezone.status_code == 422


def test_provider_url_is_validated_before_it_is_saved(client: TestClient) -> None:
    response = client.put(
        "/api/settings",
        json={"providers": {"screening": {"api_base_url": "not-a-url"}}},
    )
    assert response.status_code == 422
    assert "absolute HTTP(S) URL" in response.text


def test_stage_api_keys_survive_auto_save_without_leaking(client: TestClient) -> None:
    saved = client.put(
        "/api/settings",
        json={
            "providers": {
                "prefilter": {"api_key": "prefilter-secret"},
                "deep": {"api_key": "deep-secret"},
            }
        },
    )
    assert saved.status_code == 200
    assert saved.json()["providers"]["prefilter"]["api_key_configured"] is True
    assert saved.json()["providers"]["deep"]["api_key_configured"] is True
    assert "prefilter-secret" not in saved.text
    assert "deep-secret" not in saved.text

    autosaved = client.put(
        "/api/settings",
        json={"providers": {"screening": {"model_name": "screening-model"}}},
    )
    assert autosaved.json()["providers"]["screening"]["model_name"] == "screening-model"
    assert autosaved.json()["providers"]["prefilter"]["api_key_configured"] is True
    assert autosaved.json()["providers"]["deep"]["api_key_configured"] is True
