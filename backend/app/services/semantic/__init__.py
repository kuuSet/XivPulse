"""Application boundary for local semantic retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ...settings import ProcessSettings, get_process_settings
from .matcher import SemanticMatch, SemanticMatcher
from .model import EmbeddingModel, SemanticEmbeddingError
from .recall import RecallResult, RecallService
from .rescore import SemanticRescorer


@dataclass(frozen=True, slots=True)
class SemanticServices:
    model: EmbeddingModel
    matcher: SemanticMatcher
    recall: RecallService
    rescorer: SemanticRescorer

    @classmethod
    def create(cls, process: ProcessSettings) -> SemanticServices:
        model = EmbeddingModel(process.semantic, process.model_cache)
        matcher = SemanticMatcher(model)
        return cls(
            model,
            matcher,
            RecallService(matcher, process.semantic),
            SemanticRescorer(matcher),
        )


@lru_cache(maxsize=1)
def get_semantic_services() -> SemanticServices:
    return SemanticServices.create(get_process_settings())


__all__ = [
    "EmbeddingModel",
    "RecallResult",
    "RecallService",
    "SemanticEmbeddingError",
    "SemanticMatch",
    "SemanticMatcher",
    "SemanticRescorer",
    "SemanticServices",
    "get_semantic_services",
]
