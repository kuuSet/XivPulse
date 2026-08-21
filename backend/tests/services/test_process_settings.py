"""Process settings tests."""

from __future__ import annotations

import json
from pathlib import Path

import app.settings.process as settings_module
import pytest


def test_process_settings_read_xivpulse_environment_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XIVPULSE_MODEL_NAME", "current-model")
    monkeypatch.setenv(
        "XIVPULSE_SERVER_ALLOWED_HOSTS",
        '["localhost", "127.0.0.1"]',
    )
    settings = settings_module.ProcessSettings()
    assert settings.provider.model_name == "current-model"
    assert settings.server_allowed_hosts == ("localhost", "127.0.0.1")


def test_process_settings_derive_storage_from_one_data_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "xivpulse-data"
    monkeypatch.setenv("XIVPULSE_DATA_DIR", str(data_dir))

    settings = settings_module.ProcessSettings()

    assert settings.data_dir == data_dir.resolve()
    assert settings.database_url == f"sqlite:///{data_dir.resolve().as_posix()}/xivpulse.db"


def test_environment_key_fallback_and_secrets_are_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XIVPULSE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("XIVPULSE_AUTH_SESSION_SECRET", "session-secret")
    settings = settings_module.ProcessSettings()

    assert settings.provider.api_key == "openai-secret"
    assert "openai-secret" not in repr(settings.provider)
    assert "session-secret" not in repr(settings)
    assert "openai-secret" not in json.dumps(settings.model_dump(), default=str)
    assert "session-secret" not in json.dumps(settings.model_dump(), default=str)
