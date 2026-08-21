"""Structured model adapter factory tests."""

from pathlib import Path

from app.integrations.codex.client import CodexAdapter
from app.integrations.llm.factory import create_model_adapter
from app.settings import ProcessSettings, ProviderSettings


def test_model_adapter_factory_uses_selected_source(tmp_path: Path) -> None:
    provider = ProviderSettings(model_source="codex")
    process = ProcessSettings(data_dir=tmp_path, provider=provider)

    assert isinstance(create_model_adapter(provider, process=process), CodexAdapter)
