"""Docker scheduler timing tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.database import init_db, make_engine, make_session_factory


def test_docker_scheduler_runs_at_most_once_per_local_day() -> None:
    from app.models import Job
    from app.modules.jobs import scheduler

    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    factory = make_session_factory(engine)

    before = datetime(2026, 8, 14, 5, 29, tzinfo=UTC)  # 13:29 Asia/Shanghai
    after = datetime(2026, 8, 14, 5, 31, tzinfo=UTC)
    target = date(2026, 8, 14)
    assert scheduler.should_run_now(before, target_date=target, session_factory=factory) is False
    assert scheduler.should_run_now(after, target_date=target, session_factory=factory) is True

    with factory() as session:
        session.add(
            Job(
                kind="daily",
                status="completed",
                created_at=after,
                result_json={"fetch_date": "2026-08-13"},
            )
        )
        session.commit()
    assert scheduler.should_run_now(after, target_date=target, session_factory=factory) is True

    with factory() as session:
        session.add(
            Job(
                kind="daily",
                status="completed",
                created_at=after,
                result_json={"fetch_date": target.isoformat()},
            )
        )
        session.commit()
    assert scheduler.should_run_now(after, target_date=target, session_factory=factory) is False
    assert (
        scheduler.should_run_now(
            datetime(2026, 8, 15, 5, 31, tzinfo=UTC),
            target_date=date(2026, 8, 15),
            session_factory=factory,
        )
        is True
    )
    engine.dispose()
