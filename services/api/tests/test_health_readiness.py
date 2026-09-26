"""Liveness (/health) and readiness (/health/ready) answer different questions.

A hosted deployment needs both: the platform restarts a process that is not
alive, and an operator or an uptime check needs to tell "up" from "able to
serve" — a process whose database is gone is alive and useless. These pin that
readiness names each dependency on its own, fails with a 503, and never
reports an exception's text (which can carry hostnames and credentials).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.api.deps import get_db
from app.main import app
from app.services.resume.reaper import ReaperLoop


class _Session:
    """Stands in for the request's session and answers only the readiness query."""

    def __init__(self, *, rows=None, error: Exception | None = None) -> None:
        self._rows = rows or []
        self._error = error

    def execute(self, *_args, **_kwargs):
        if self._error is not None:
            raise self._error
        return iter(self._rows)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


@contextmanager
def _database(session: _Session):
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous


class _Reaper:
    def __init__(self, healthy: bool) -> None:
        self.is_healthy = healthy


@contextmanager
def _reaper(client, reaper):
    real = client.app.state.reaper
    client.app.state.reaper = reaper
    try:
        yield
    finally:
        client.app.state.reaper = real


def test_ready_when_the_database_is_at_head_and_processing_runs(client):
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "easyapply-api",
        "checks": {"database": "ok", "schema": "ok", "processing": "ok"},
    }


def test_an_unreachable_database_is_a_503_that_names_it_and_nothing_else(client):
    failure = OperationalError(
        "SELECT version_num FROM alembic_version",
        {},
        Exception('connection to server at "db-secret-host.internal" failed: password authentication failed'),
    )
    with _database(_Session(error=failure)):
        response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"database": "unavailable", "schema": "unknown", "processing": "ok"}
    assert "db-secret-host" not in response.text
    assert "password" not in response.text


def test_a_database_that_was_never_migrated_is_not_ready(client):
    missing = ProgrammingError("SELECT version_num FROM alembic_version", {}, Exception("relation does not exist"))
    with _database(_Session(error=missing)):
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "schema": "missing", "processing": "ok"}


def test_a_database_at_another_revision_is_not_ready(client):
    with _database(_Session(rows=[("0000000000aa",)])):
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["schema"] == "mismatch"


def test_a_stopped_reaper_is_not_ready(client):
    with _reaper(client, _Reaper(healthy=False)):
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "schema": "ok", "processing": "stopped"}


def test_liveness_does_not_depend_on_the_database(client):
    failure = OperationalError("SELECT 1", {}, Exception("down"))
    with _database(_Session(error=failure)):
        assert client.get("/health").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_readiness_is_behind_the_host_guard(client):
    response = client.get("/health/ready", headers={"Host": "evil.example"})

    assert response.status_code == 403
    assert response.json()["code"] == "host_not_allowed"


def test_a_reaper_switched_off_by_configuration_is_healthy():
    assert ReaperLoop(enabled=False).is_healthy


@pytest.mark.parametrize("interval", [0, -1])
def test_a_reaper_with_no_interval_is_healthy(interval):
    assert ReaperLoop(enabled=True, interval_seconds=interval).is_healthy


def test_an_enabled_reaper_is_healthy_only_while_its_thread_runs():
    reaper = ReaperLoop(enabled=True, interval_seconds=3600)
    assert not reaper.is_healthy

    reaper.start()
    try:
        assert reaper.is_healthy
    finally:
        reaper.stop()

    assert not reaper.is_healthy
