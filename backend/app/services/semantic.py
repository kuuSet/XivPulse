"""Local multilingual semantic retrieval with a persistent SQLite vector cache."""

from __future__ import annotations

import hashlib
import threading
from array import array
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..env import xivpulse_env
from ..models import Paper, PaperEmbedding, Topic

MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_DIMENSIONS = 384
RECALL_THRESHOLDS = {"high": 0.76, "balanced": 0.80, "precise": 0.84}

_model: Any | None = None
_model_lock = threading.Lock()


class SemanticEmbeddingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    score: float
    topic_scores: dict[str, float]

    @property
    def candidate_topics(self) -> list[str]:
        return [name for name, _ in sorted(self.topic_scores.items(), key=lambda item: -item[1])]


def model_cache_dir() -> Path:
    configured = str(xivpulse_env("MODEL_CACHE", "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[3] / "data" / "model-cache").resolve()


def _load_model() -> Any:
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from fastembed import TextEmbedding
            from fastembed.common.model_description import ModelSource, PoolingType

            supported = {item["model"].casefold() for item in TextEmbedding.list_supported_models()}
            if MODEL_ID.casefold() not in supported:
                TextEmbedding.add_custom_model(
                    model=MODEL_ID,
                    pooling=PoolingType.MEAN,
                    normalization=True,
                    sources=ModelSource(hf=MODEL_ID),
                    dim=MODEL_DIMENSIONS,
                    model_file="onnx/model.onnx",
                    description="Multilingual E5 small ONNX model",
                )
            cache_dir = model_cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            _model = TextEmbedding(model_name=MODEL_ID, cache_dir=str(cache_dir))
        except Exception as exc:  # model download and ONNX failures need a clear pipeline error
            raise SemanticEmbeddingError(f"本地语义模型不可用：{exc}") from exc
    return _model


def embedding_status(*, warm: bool = False) -> dict[str, Any]:
    error: str | None = None
    if warm:
        try:
            list(_load_model().embed(["query: arXiv semantic retrieval"], batch_size=1))
        except SemanticEmbeddingError as exc:
            error = str(exc)
    cache = model_cache_dir()
    cache_bytes = 0
    if cache.exists():
        try:
            cache_bytes = sum(path.stat().st_size for path in cache.rglob("*") if path.is_file())
        except OSError:
            cache_bytes = 0
    return {
        "ok": error is None,
        "available": error is None,
        "model": MODEL_ID,
        "dimensions": MODEL_DIMENSIONS,
        "cache_dir": str(cache),
        "cache_bytes": cache_bytes,
        "loaded": _model is not None,
        "error": error,
        "message": error or ("本地语义模型已加载" if _model is not None else "等待首次加载"),
        "token_cost": 0,
    }


def recall_mode(value: object) -> str:
    mode = str(value or "high").casefold()
    return mode if mode in RECALL_THRESHOLDS else "high"


def is_candidate(
    *,
    semantic_score: float | None,
    mode: str,
    watched_author: bool = False,
) -> bool:
    selected = recall_mode(mode)
    return bool(
        watched_author
        or (semantic_score is not None and semantic_score >= RECALL_THRESHOLDS[selected])
    )


def _values(item: Topic | Mapping[str, Any], name: str) -> list[str]:
    raw = (
        item.get(name, item.get(f"{name}_json", []))
        if isinstance(item, Mapping)
        else getattr(item, name)
    )
    return [str(value).strip() for value in raw or [] if str(value).strip()]


def topic_text(topic: Topic | Mapping[str, Any]) -> str:
    name = str(topic.get("name", "") if isinstance(topic, Mapping) else topic.name).strip()
    aliases = _values(topic, "aliases")
    keywords = _values(topic, "keywords")
    anchors = _values(topic, "context_keywords")
    parts = [f"研究方向：{name}"]
    if aliases:
        parts.append(f"别名：{'；'.join(aliases)}")
    if keywords:
        parts.append(f"核心概念：{'；'.join(keywords)}")
    if anchors:
        parts.append(f"应用语境：{'；'.join(anchors)}")
    return "query: " + "。".join(parts)


def paper_text(paper: Paper | Mapping[str, Any]) -> str:
    if isinstance(paper, Mapping):
        title = str(paper.get("title", ""))
        abstract = str(paper.get("abstract", ""))
    else:
        title, abstract = paper.title, paper.abstract
    return f"passage: {title.strip()}\n{abstract.strip()}"


def content_hash(paper: Paper | Mapping[str, Any]) -> str:
    return hashlib.sha256(paper_text(paper).encode("utf-8")).hexdigest()


def _to_blob(vector: Iterable[float]) -> bytes:
    values = array("f", (float(value) for value in vector))
    if len(values) != MODEL_DIMENSIONS:
        raise SemanticEmbeddingError(
            f"语义模型返回 {len(values)} 维向量，预期为 {MODEL_DIMENSIONS} 维"
        )
    return values.tobytes()


def _from_blob(blob: bytes, dimensions: int) -> array[float]:
    values = array("f")
    values.frombytes(blob)
    if len(values) != dimensions:
        raise SemanticEmbeddingError("数据库中的论文向量维度不正确")
    return values


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return float(sum(float(a) * float(b) for a, b in zip(left, right, strict=True)))


def match_papers(
    session: Session,
    papers: Sequence[Paper],
    topics: Sequence[Topic],
) -> dict[int, SemanticMatch]:
    enabled_topics = [topic for topic in topics if topic.enabled]
    if not papers or not enabled_topics:
        return {}

    model = _load_model()
    topic_vectors = list(
        model.embed([topic_text(topic) for topic in enabled_topics], batch_size=32)
    )
    if len(topic_vectors) != len(enabled_topics):
        raise SemanticEmbeddingError("语义模型没有返回完整的研究方向向量")

    paper_ids = [paper.id for paper in papers]
    cached = {
        item.paper_id: item
        for item in session.scalars(
            select(PaperEmbedding).where(
                PaperEmbedding.paper_id.in_(paper_ids), PaperEmbedding.model_id == MODEL_ID
            )
        ).all()
    }
    vectors: dict[int, array[float]] = {}
    missing: list[Paper] = []
    for paper in papers:
        stored = cached.get(paper.id)
        digest = content_hash(paper)
        if stored is None or stored.content_hash != digest or stored.dimensions != MODEL_DIMENSIONS:
            missing.append(paper)
            continue
        try:
            vectors[paper.id] = _from_blob(stored.vector_blob, stored.dimensions)
        except SemanticEmbeddingError:
            missing.append(paper)

    if missing:
        generated = list(model.embed([paper_text(paper) for paper in missing], batch_size=32))
        if len(generated) != len(missing):
            raise SemanticEmbeddingError("语义模型没有返回完整的论文向量")
        for paper, vector in zip(missing, generated, strict=True):
            blob = _to_blob(vector)
            values = _from_blob(blob, MODEL_DIMENSIONS)
            vectors[paper.id] = values
            stored = cached.get(paper.id)
            if stored is None:
                stored = PaperEmbedding(paper_id=paper.id, model_id=MODEL_ID)
                session.add(stored)
            stored.content_hash = content_hash(paper)
            stored.dimensions = MODEL_DIMENSIONS
            stored.vector_blob = blob

    results: dict[int, SemanticMatch] = {}
    for paper in papers:
        vector = vectors[paper.id]
        scores = {
            topic.name: round(_dot(vector, topic_vector), 6)
            for topic, topic_vector in zip(enabled_topics, topic_vectors, strict=True)
        }
        ordered = dict(sorted(scores.items(), key=lambda item: -item[1]))
        results[paper.id] = SemanticMatch(
            score=max(ordered.values(), default=0.0), topic_scores=ordered
        )
    session.flush()
    return results


__all__ = [
    "MODEL_DIMENSIONS",
    "MODEL_ID",
    "RECALL_THRESHOLDS",
    "SemanticEmbeddingError",
    "SemanticMatch",
    "embedding_status",
    "is_candidate",
    "match_papers",
    "recall_mode",
]
