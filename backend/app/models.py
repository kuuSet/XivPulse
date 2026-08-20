"""SQLAlchemy persistence models for the local, single-user application."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Profile(TimestampMixin, Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    topics: Mapped[list[Topic]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", order_by="Topic.id"
    )
    matches: Mapped[list[PaperMatch]] = relationship(back_populates="profile")
    analyses: Mapped[list[Analysis]] = relationship(back_populates="profile")
    feedback_items: Mapped[list[Feedback]] = relationship(back_populates="profile")
    digests: Mapped[list[Digest]] = relationship(back_populates="profile")
    daily_research_reports: Mapped[list[DailyResearchReport]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    report_synthesis_runs: Mapped[list[ReportSynthesisRun]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    reading_states: Mapped[list[PaperReadingState]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    projects: Mapped[list[ResearchProject]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    aliases_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    query: Mapped[str] = mapped_column(Text, default="")
    arxiv_categories_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    keywords_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    context_keywords_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    negative_keywords_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    profile: Mapped[Profile] = relationship(back_populates="topics")

    @property
    def aliases(self) -> list[str]:
        return list(self.aliases_json or [])

    @aliases.setter
    def aliases(self, value: list[str]) -> None:
        self.aliases_json = list(value)

    @property
    def arxiv_categories(self) -> list[str]:
        return list(self.arxiv_categories_json or [])

    @arxiv_categories.setter
    def arxiv_categories(self, value: list[str]) -> None:
        self.arxiv_categories_json = list(value)

    @property
    def keywords(self) -> list[str]:
        return list(self.keywords_json or [])

    @keywords.setter
    def keywords(self, value: list[str]) -> None:
        self.keywords_json = list(value)

    @property
    def context_keywords(self) -> list[str]:
        return list(self.context_keywords_json or [])

    @context_keywords.setter
    def context_keywords(self, value: list[str]) -> None:
        self.context_keywords_json = list(value)

    @property
    def negative_keywords(self) -> list[str]:
        return list(self.negative_keywords_json or [])

    @negative_keywords.setter
    def negative_keywords(self, value: list[str]) -> None:
        self.negative_keywords_json = list(value)


class Paper(TimestampMixin, Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    arxiv_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    latest_version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str] = mapped_column(Text, default="")
    authors_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    affiliations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    categories_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    primary_category: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    journal_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    abs_url: Mapped[str] = mapped_column(Text, default="")
    html_url: Mapped[str] = mapped_column(Text, default="")
    pdf_url: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    arxiv_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    ingestion_source: Mapped[str] = mapped_column(String(30), default="atom")
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False)

    versions: Mapped[list[PaperVersion]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="PaperVersion.version"
    )
    matches: Mapped[list[PaperMatch]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    analyses: Mapped[list[Analysis]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="Analysis.created_at"
    )
    feedback_items: Mapped[list[Feedback]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    reading_states: Mapped[list[PaperReadingState]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    project_links: Mapped[list[ProjectPaper]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list[PaperEmbedding]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )

    @property
    def authors(self) -> list[str]:
        return list(self.authors_json or [])

    @authors.setter
    def authors(self, value: list[str]) -> None:
        self.authors_json = list(value)

    @property
    def affiliations(self) -> list[str]:
        return list(self.affiliations_json or [])

    @affiliations.setter
    def affiliations(self, value: list[str]) -> None:
        self.affiliations_json = list(value)

    @property
    def categories(self) -> list[str]:
        return list(self.categories_json or [])

    @categories.setter
    def categories(self, value: list[str]) -> None:
        self.categories_json = list(value)

    @property
    def version(self) -> int:
        return self.latest_version


class PaperVersion(Base):
    __tablename__ = "paper_versions"
    __table_args__ = (
        UniqueConstraint("paper_id", "version", name="uq_paper_version"),
        UniqueConstraint("arxiv_version_id", name="uq_arxiv_version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    arxiv_version_id: Mapped[str] = mapped_column(String(72))
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str] = mapped_column(Text, default="")
    authors_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    affiliations_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    categories_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    abs_url: Mapped[str] = mapped_column(Text, default="")
    html_url: Mapped[str] = mapped_column(Text, default="")
    pdf_url: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arxiv_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    paper: Mapped[Paper] = relationship(back_populates="versions")

    @property
    def authors(self) -> list[str]:
        return list(self.authors_json or [])

    @property
    def affiliations(self) -> list[str]:
        return list(self.affiliations_json or [])

    @affiliations.setter
    def affiliations(self, value: list[str]) -> None:
        self.affiliations_json = list(value)

    @property
    def categories(self) -> list[str]:
        return list(self.categories_json or [])


class PaperMatch(Base):
    __tablename__ = "paper_matches"
    __table_args__ = (UniqueConstraint("paper_id", "profile_id", name="uq_paper_profile_match"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    matched_topics_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    matched_keywords_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_keywords_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    lexical_score: Mapped[float] = mapped_column(Float, default=0.0)
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    semantic_topics_json: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    semantic_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    screening_relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    screened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    paper: Mapped[Paper] = relationship(back_populates="matches")
    profile: Mapped[Profile] = relationship(back_populates="matches")
    topic: Mapped[Topic | None] = relationship()

    @property
    def matched_topics(self) -> list[str]:
        return list(self.matched_topics_json or [])

    @property
    def matched_keywords(self) -> list[str]:
        return list(self.matched_keywords_json or [])

    @property
    def semantic_topics(self) -> dict[str, float]:
        return {
            str(name): float(score)
            for name, score in dict(self.semantic_topics_json or {}).items()
            if isinstance(score, int | float)
        }

    @property
    def effective_score(self) -> float:
        if self.screening_relevance_score is not None:
            return max(0.0, min(100.0, self.screening_relevance_score))
        return max(0.0, min(100.0, (self.semantic_score or 0.0) * 100))

    @property
    def score_source(self) -> str:
        return "ai" if self.screening_relevance_score is not None else "semantic"

    @classmethod
    def effective_score_expression(cls):
        return func.coalesce(cls.screening_relevance_score, cls.semantic_score * 100, 0)


class PaperEmbedding(TimestampMixin, Base):
    __tablename__ = "paper_embeddings"
    __table_args__ = (UniqueConstraint("paper_id", "model_id", name="uq_paper_embedding_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[str] = mapped_column(String(160), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    dimensions: Mapped[int] = mapped_column(Integer)
    vector_blob: Mapped[bytes] = mapped_column(LargeBinary)

    paper: Mapped[Paper] = relationship(back_populates="embeddings")


class ScreeningQueueItem(TimestampMixin, Base):
    __tablename__ = "screening_queue"
    __table_args__ = (
        UniqueConstraint("paper_id", "profile_id", name="uq_screening_queue_paper_profile"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    report_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    recall_mode: Mapped[str] = mapped_column(String(20), default="high")
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    semantic_topics_json: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    paper: Mapped[Paper] = relationship()
    profile: Mapped[Profile] = relationship()


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="screening", index=True)
    status: Mapped[str] = mapped_column(String(20), default="completed", index=True)
    provider: Mapped[str] = mapped_column(String(80), default="unknown")
    model: Mapped[str] = mapped_column(String(120), default="unknown")
    transport: Mapped[str] = mapped_column(String(30), default="unknown")
    content_source: Mapped[str] = mapped_column(String(20), default="abstract")
    budget_mode: Mapped[str] = mapped_column(String(20), default="balanced", index=True)
    usage_available: Mapped[bool] = mapped_column(Boolean, default=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    verdict: Mapped[str] = mapped_column(Text, default="")
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    novelty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rigor_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    impact_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    strengths_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    weaknesses_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    paper: Mapped[Paper] = relationship(back_populates="analyses")
    profile: Mapped[Profile] = relationship(back_populates="analyses")

    @property
    def strengths(self) -> list[str]:
        return list(self.strengths_json or [])

    @property
    def weaknesses(self) -> list[str]:
        return list(self.weaknesses_json or [])

    @property
    def tags(self) -> list[str]:
        return list(self.tags_json or [])

    @property
    def payload(self) -> dict[str, Any]:
        return dict(self.payload_json or {})


class Feedback(TimestampMixin, Base):
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("paper_id", "profile_id", name="uq_paper_profile_feedback"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(30), default="neutral", index=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")

    paper: Mapped[Paper] = relationship(back_populates="feedback_items")
    profile: Mapped[Profile] = relationship(back_populates="feedback_items")
    reasons: Mapped[list[FeedbackReason]] = relationship(
        back_populates="feedback", cascade="all, delete-orphan", order_by="FeedbackReason.id"
    )


class FeedbackReason(Base):
    """Structured feedback stored separately to keep old SQLite databases additive-only."""

    __tablename__ = "feedback_reasons"
    __table_args__ = (UniqueConstraint("feedback_id", "code", name="uq_feedback_reason_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feedback_id: Mapped[int] = mapped_column(
        ForeignKey("feedback.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40), index=True)

    feedback: Mapped[Feedback] = relationship(back_populates="reasons")


class PaperReadingState(TimestampMixin, Base):
    __tablename__ = "paper_reading_states"
    __table_args__ = (UniqueConstraint("paper_id", "profile_id", name="uq_paper_profile_reading"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(20), default="unread", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)

    paper: Mapped[Paper] = relationship(back_populates="reading_states")
    profile: Mapped[Profile] = relationship(back_populates="reading_states")

    @property
    def tags(self) -> list[str]:
        return list(self.tags_json or [])

    @tags.setter
    def tags(self, value: list[str]) -> None:
        self.tags_json = list(value)


class ResearchProject(TimestampMixin, Base):
    __tablename__ = "research_projects"
    __table_args__ = (UniqueConstraint("profile_id", "name", name="uq_profile_project_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(30), default="custom", index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    color: Mapped[str] = mapped_column(String(20), default="#2563eb")
    goal: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    profile: Mapped[Profile] = relationship(back_populates="projects")
    paper_links: Mapped[list[ProjectPaper]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectPaper.created_at"
    )


class ProjectPaper(TimestampMixin, Base):
    __tablename__ = "project_papers"
    __table_args__ = (UniqueConstraint("project_id", "paper_id", name="uq_project_paper"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    reading_state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)

    project: Mapped[ResearchProject] = relationship(back_populates="paper_links")
    paper: Mapped[Paper] = relationship(back_populates="project_links")

    @property
    def tags(self) -> list[str]:
        return list(self.tags_json or [])

    @tags.setter
    def tags(self, value: list[str]) -> None:
        self.tags_json = list(value)


class Digest(Base):
    __tablename__ = "digests"
    __table_args__ = (UniqueConstraint("profile_id", "digest_date", name="uq_profile_digest_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    digest_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    paper_ids_json: Mapped[list[int]] = mapped_column(JSON, default=list)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped[Profile] = relationship(back_populates="digests")

    @property
    def paper_ids(self) -> list[int]:
        return list(self.paper_ids_json or [])

    @property
    def stats(self) -> dict[str, Any]:
        return dict(self.stats_json or {})


class DailyResearchReport(TimestampMixin, Base):
    """Immutable-by-default JSON snapshot of one daily research report."""

    __tablename__ = "daily_research_reports"
    __table_args__ = (
        UniqueConstraint("profile_id", "report_date", name="uq_profile_daily_research_report_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    report_date: Mapped[date] = mapped_column(Date, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    profile: Mapped[Profile] = relationship(back_populates="daily_research_reports")

    @property
    def payload(self) -> dict[str, Any]:
        return dict(self.payload_json or {})


class ReportSynthesisRun(TimestampMixin, Base):
    """One auditable provider attempt to synthesize a saved daily report."""

    __tablename__ = "report_synthesis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    report_id: Mapped[int] = mapped_column(
        ForeignKey("daily_research_reports.id", ondelete="CASCADE"), index=True
    )
    report_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    transport: Mapped[str] = mapped_column(String(30), default="")
    budget_mode: Mapped[str] = mapped_column(String(20), default="balanced", index=True)
    usage_available: Mapped[bool] = mapped_column(Boolean, default=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile: Mapped[Profile] = relationship(back_populates="report_synthesis_runs")
    report: Mapped[DailyResearchReport] = relationship()

    @property
    def payload(self) -> dict[str, Any]:
        return dict(self.payload_json or {})


class EmailDelivery(TimestampMixin, Base):
    __tablename__ = "email_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "report_date",
            "recipient",
            "delivery_kind",
            name="uq_email_delivery_report_recipient_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    report_date: Mapped[date] = mapped_column(Date, index=True)
    recipient: Mapped[str] = mapped_column(String(320))
    delivery_kind: Mapped[str] = mapped_column(String(30), default="automatic")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped[str] = mapped_column(String(500), default="")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    profile: Mapped[Profile] = relationship()


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def result(self) -> dict[str, Any]:
        return dict(self.result_json or {})


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

__all__ = [
    "Analysis",
    "AppSetting",
    "DailyResearchReport",
    "Digest",
    "EmailDelivery",
    "Feedback",
    "FeedbackReason",
    "Job",
    "Paper",
    "PaperEmbedding",
    "PaperMatch",
    "PaperReadingState",
    "PaperVersion",
    "Profile",
    "ProjectPaper",
    "ResearchProject",
    "ReportSynthesisRun",
    "ScreeningQueueItem",
    "Topic",
    "utcnow",
]
