"""Select the configured structured-model transport."""

from ..integrations.codex.client import CodexAdapter
from ..settings import ProcessSettings, ProviderSettings
from .provider import OpenAICompatibleAdapter, StructuredModelAdapter


def create_model_adapter(
    settings: ProviderSettings,
    *,
    process: ProcessSettings | None = None,
) -> StructuredModelAdapter:
    if settings.model_source == "codex":
        return CodexAdapter(settings, process=process)
    return OpenAICompatibleAdapter(settings)


__all__ = ["create_model_adapter"]
