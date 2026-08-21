from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_apps_use_independent_databases(app_factory: Callable[[], FastAPI]) -> None:
    first_app = app_factory()
    second_app = app_factory()

    with TestClient(first_app) as first, TestClient(second_app) as second:
        response = first.put("/api/settings", json={"prefilter_batch_size": 47})

        assert response.status_code == 200
        assert first.get("/api/settings").json()["prefilter_batch_size"] == 47
        assert second.get("/api/settings").json()["prefilter_batch_size"] == 30
