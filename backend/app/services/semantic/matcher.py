"""Semantic paper matching with a persistent SQLite vector cache."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Paper, PaperEmbedding, Topic
from .model import EmbeddingModel, SemanticEmbeddingError


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    score: float
    topic_scores: dict[str, float]

    @property
    def candidate_topics(self) -> list[str]:
        return list(self.topic_scores)


class SemanticMatcher:
    def __init__(self, model: EmbeddingModel) -> None:
        self.model = model

    @property
    def model_name(self) -> str:
        return self.model.model_name

    def fingerprint(self, topics: Sequence[Topic]) -> str:
        """Identify the model and enabled direction text that determine semantic scores."""

        configured = [
            {
                "name": topic.name.strip(),
                "aliases": _values(topic, "aliases"),
                "keywords": _values(topic, "keywords"),
                "context_keywords": _values(topic, "context_keywords"),
                "text": _topic_text(topic),
            }
            for topic in topics
            if topic.enabled
        ]
        configured.sort(
            key=lambda item: (
                str(item["name"]).casefold(),
                json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
        )
        payload = json.dumps(
            {"model": self.model_name, "topics": configured},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def match(
        self,
        session: Session,
        papers: Sequence[Paper],
        topics: Sequence[Topic],
    ) -> dict[int, SemanticMatch]:
        enabled_topics = [topic for topic in topics if topic.enabled]
        if not papers or not enabled_topics:
            return {}

        topic_vectors = self.model.embed_queries([_topic_text(topic) for topic in enabled_topics])
        dimensions = self.model.dimensions
        if dimensions is None:
            raise SemanticEmbeddingError("语义模型没有提供向量维度")

        paper_ids = [paper.id for paper in papers]
        cached = {
            item.paper_id: item
            for item in session.scalars(
                select(PaperEmbedding).where(
                    PaperEmbedding.paper_id.in_(paper_ids),
                    PaperEmbedding.model_id == self.model_name,
                )
            ).all()
        }
        digests = {paper.id: _content_hash(paper) for paper in papers}
        vectors: dict[int, np.ndarray] = {}
        missing: list[Paper] = []
        for paper in papers:
            stored = cached.get(paper.id)
            if (
                stored is None
                or stored.content_hash != digests[paper.id]
                or stored.dimensions != dimensions
            ):
                missing.append(paper)
                continue
            try:
                vectors[paper.id] = _decode_vector(stored.vector_blob, dimensions)
            except SemanticEmbeddingError:
                missing.append(paper)

        generated = self.model.embed_passages([_paper_text(paper) for paper in missing])
        for paper, vector in zip(missing, generated, strict=True):
            blob = _encode_vector(vector, dimensions)
            vectors[paper.id] = vector
            stored = cached.get(paper.id)
            if stored is None:
                stored = PaperEmbedding(paper_id=paper.id, model_id=self.model_name)
                session.add(stored)
            stored.content_hash = digests[paper.id]
            stored.dimensions = dimensions
            stored.vector_blob = blob

        results: dict[int, SemanticMatch] = {}
        for paper in papers:
            scores = {
                topic.name: round(_dot(vectors[paper.id], topic_vector), 6)
                for topic, topic_vector in zip(enabled_topics, topic_vectors, strict=True)
            }
            ordered = dict(sorted(scores.items(), key=lambda item: -item[1]))
            results[paper.id] = SemanticMatch(
                score=max(ordered.values(), default=0.0),
                topic_scores=ordered,
            )
        session.flush()
        return results


def _values(item: Topic | Mapping[str, Any], name: str) -> list[str]:
    raw = (
        item.get(name, item.get(f"{name}_json", []))
        if isinstance(item, Mapping)
        else getattr(item, name)
    )
    return [str(value).strip() for value in raw or [] if str(value).strip()]


def _topic_text(topic: Topic | Mapping[str, Any]) -> str:
    name = str(topic.get("name", "") if isinstance(topic, Mapping) else topic.name).strip()
    aliases = _values(topic, "aliases")
    keywords = _values(topic, "keywords")
    context = _values(topic, "context_keywords")
    parts: list[str] = []
    if aliases:
        parts.append(f"Research area: {', '.join(aliases)}")
    if keywords:
        parts.append(f"Core concepts: {', '.join(keywords)}")
    if context:
        parts.append(f"Application context: {', '.join(context)}")
    return ". ".join(parts or [f"Research area: {name}"])


def _paper_text(paper: Paper | Mapping[str, Any]) -> str:
    if isinstance(paper, Mapping):
        title = str(paper.get("title", ""))
        abstract = str(paper.get("abstract", ""))
    else:
        title, abstract = paper.title, paper.abstract
    return f"{title.strip()}\n{abstract.strip()}"


def _content_hash(paper: Paper | Mapping[str, Any]) -> str:
    return hashlib.sha256(_paper_text(paper).encode("utf-8")).hexdigest()


def _encode_vector(vector: np.ndarray, dimensions: int) -> bytes:
    if vector.size != dimensions:
        raise SemanticEmbeddingError(f"语义模型返回 {vector.size} 维向量，预期为 {dimensions} 维")
    return vector.tobytes()


def _decode_vector(blob: bytes, dimensions: int) -> np.ndarray:
    values = np.frombuffer(blob, dtype=np.float32)
    if values.size != dimensions:
        raise SemanticEmbeddingError("数据库中的论文向量维度不正确")
    return values


def _dot(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


__all__ = ["SemanticMatch", "SemanticMatcher"]
