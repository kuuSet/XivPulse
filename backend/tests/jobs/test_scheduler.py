"""Docker scheduler timing tests."""

from __future__ import annotations

from datetime import UTC, datetime

from app.database import init_db, make_engine, make_session_factory


def test_docker_scheduler_runs_at_most_once_per_local_day() -> None:
    from app.jobs import scheduler
    from app.models import Job

    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    factory = make_session_factory(engine)

    before = datetime(2026, 8, 14, 5, 29, tzinfo=UTC)  # 13:29 Asia/Shanghai
    after = datetime(2026, 8, 14, 5, 31, tzinfo=UTC)
    assert scheduler.should_run_now(before, session_factory=factory) is False
    assert scheduler.should_run_now(after, session_factory=factory) is True

    with factory() as session:
        session.add(Job(kind="daily", status="completed", created_at=after))
        session.commit()
    assert scheduler.should_run_now(after, session_factory=factory) is False
    assert (
        scheduler.should_run_now(datetime(2026, 8, 15, 5, 31, tzinfo=UTC), session_factory=factory)
        is True
    )
    engine.dispose()
