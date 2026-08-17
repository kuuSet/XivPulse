"""Lifecycle management for the local FastEmbed model."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ...settings import SemanticSettings


class SemanticEmbeddingError(RuntimeError):
    pass


class EmbeddingModel:
    def __init__(
        self,
        settings: SemanticSettings,
        cache_dir: Path,
        *,
        backend: Any | None = None,
    ) -> None:
        self.settings = settings
        self.cache_dir = cache_dir
        self._backend = backend
        self._dimensions: int | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self.settings.model_name

    @property
    def dimensions(self) -> int | None:
        return self._dimensions

    @property
    def loaded(self) -> bool:
        return self._backend is not None

    def embed_queries(self, texts: Sequence[str]) -> list[np.ndarray]:
        return self._embed("query_embed", texts)

    def embed_passages(self, texts: Sequence[str]) -> list[np.ndarray]:
        return self._embed("passage_embed", texts)

    def warmup(self) -> None:
        self.embed_queries(["arXiv semantic retrieval"])

    def status(self, *, warm: bool = False) -> dict[str, Any]:
        error: str | None = None
        if warm:
            try:
                self.warmup()
            except SemanticEmbeddingError as exc:
                error = str(exc)
        return {
            "ok": error is None,
            "available": error is None,
            "model": self.model_name,
            "dimensions": self.dimensions,
            "cache_dir": str(self.cache_dir),
            "cache_bytes": self._cache_size(),
            "loaded": self.loaded,
            "error": error,
            "message": error or ("本地语义模型已加载" if self.loaded else "等待首次加载"),
            "token_cost": 0,
        }

    def _load(self) -> Any:
        if self._backend is not None:
            return self._backend
        with self._lock:
            if self._backend is not None:
                return self._backend
            try:
                from fastembed import TextEmbedding

                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._backend = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=str(self.cache_dir),
                    threads=self.settings.threads,
                )
            except Exception as exc:
                raise SemanticEmbeddingError(f"本地语义模型不可用：{exc}") from exc
        return self._backend

    def _embed(
        self,
        method: Literal["query_embed", "passage_embed"],
        texts: Sequence[str],
    ) -> list[np.ndarray]:
        if not texts:
            return []
        try:
            generated = list(
                getattr(self._load(), method)(
                    texts,
                    batch_size=self.settings.batch_size,
                    normalize=True,
                )
            )
        except SemanticEmbeddingError:
            raise
        except Exception as exc:
            raise SemanticEmbeddingError(f"本地语义向量生成失败：{exc}") from exc
        if len(generated) != len(texts):
            raise SemanticEmbeddingError("语义模型没有返回完整向量")

        vectors = [np.asarray(vector, dtype=np.float32).reshape(-1) for vector in generated]
        dimensions = int(vectors[0].size)
        if dimensions == 0 or any(vector.size != dimensions for vector in vectors):
            raise SemanticEmbeddingError("语义模型返回了无效向量维度")
        with self._lock:
            if self._dimensions is None:
                self._dimensions = dimensions
            elif self._dimensions != dimensions:
                raise SemanticEmbeddingError(
                    f"语义模型向量维度从 {self._dimensions} 变成了 {dimensions}"
                )
        return vectors

    def _cache_size(self) -> int:
        if not self.cache_dir.exists():
            return 0
        try:
            return sum(path.stat().st_size for path in self.cache_dir.rglob("*") if path.is_file())
        except OSError:
            return 0


__all__ = ["EmbeddingModel", "SemanticEmbeddingError"]
