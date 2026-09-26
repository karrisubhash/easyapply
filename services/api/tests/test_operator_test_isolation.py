"""The operator tests do not depend on the developer's `.env`, and production config is untouched.

`app/core/config.py` builds `settings` once, at import, from whatever `.env`
sits in the working directory — for a developer, their own. A developer who
set OPERATOR_API_TOKEN (or OPERATOR_API_ENABLED=false) used to turn every
operator test that relies on the documented development posture into a 403
(or a 404). `tests/conftest.py` now pins that posture for the test process;
these tests hold both halves of the bargain:

* the suite runs the same whatever a developer `.env` says — proven in a child
  pytest process whose only `.env` is a fake one that sets BOTH operator
  settings against the posture (the suite pins its own public token and sends
  it, so a developer token left in place would make every request a 403), so
  removing either half of the pin fails it, and neither this machine's real
  `.env` nor its secret is involved;
* production still loads both settings exactly as before — proven on the
  application's own module-level `settings`, imported in a fresh interpreter,
  not just on the `Settings` class.

Nothing here reads this machine's real `.env`, and every assertion that could
see a token value compares it into a boolean first: pytest prints both sides of
a failed comparison.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.core.config import Settings, settings
from tests.conftest import OPERATOR_TEST_TOKEN

API_ROOT = Path(__file__).resolve().parents[1]
OPERATOR_API_TESTS = API_ROOT / "tests" / "test_operator_api.py"
FAKE_DEVELOPER_TOKEN = "fake-developer-operator-token"
DUMMY_DATABASE_URL = "postgresql+psycopg://u:p@localhost:5432/nowhere"

_OPERATOR_SETTINGS = frozenset({"OPERATOR_API_TOKEN", "OPERATOR_API_ENABLED"})
#: Would make the child a different pytest invocation from the one under test.
_PYTEST_VARIABLES_NOT_INHERITED = frozenset({"PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST", "PYTEST_VERSION"})
#: One fixed name, swept before and after: a parent killed mid-run leaves nothing
#: that the next run does not reclaim. Concurrent sessions already cannot share
#: the test database, so a fixed name costs nothing.
_CHILD_DATABASE_SUFFIX = "_envprobe"
_POSTGRES_IDENTIFIER_BYTES = 63

#: Imports the application's own `settings`, exactly as production does, and
#: reports through the exit code only — never a value.
_SINGLETON_PROBE = (
    "from app.core.config import settings as s\n"
    f"ok = s.OPERATOR_API_TOKEN == {FAKE_DEVELOPER_TOKEN!r} and s.OPERATOR_API_ENABLED is False\n"
    "raise SystemExit(0 if ok else 1)\n"
)


def _is_operator_setting(name: str) -> bool:
    # Upper-cased: pydantic-settings matches environment names case-insensitively.
    return name.upper() in _OPERATOR_SETTINGS


@pytest.fixture
def no_operator_environment(monkeypatch):
    """Nothing in the process environment speaks for the operator settings."""
    for name in list(os.environ):
        if _is_operator_setting(name):
            monkeypatch.delenv(name)


def _write_fake_developer_env(directory: Path) -> None:
    """A developer `.env` that contradicts the development posture on both counts."""
    (directory / ".env").write_text(
        f"OPERATOR_API_ENABLED=false\nOPERATOR_API_TOKEN={FAKE_DEVELOPER_TOKEN}\n",
        encoding="utf-8",
    )


def _child_environment(**overrides: str) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not _is_operator_setting(name)
        and name.upper() not in _PYTEST_VARIABLES_NOT_INHERITED
        and not name.upper().startswith("PYTEST_XDIST_")
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.update(overrides)
    return environment


def _run(args: list[str], *, cwd: Path, environment: dict[str, str], timeout: int) -> tuple[int, str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return completed.returncode, completed.stdout + completed.stderr


def _application_settings_load_the_fake_env(directory: Path) -> bool:
    """Does the app's module-level `settings`, built in `directory`, see the fake `.env`?"""
    returncode, _ = _run(
        [sys.executable, "-c", _SINGLETON_PROBE],
        cwd=directory,
        environment=_child_environment(PYTHONPATH=str(API_ROOT), DATABASE_URL=DUMMY_DATABASE_URL),
        timeout=120,
    )
    return returncode == 0


def _child_database_url() -> URL:
    """The child's own test database. A URL object: its repr masks the password."""
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        base = make_url(override)
    else:
        development = make_url(settings.DATABASE_URL)
        base = development.set(database=f"{development.database}_test")
    name = f"{base.database}{_CHILD_DATABASE_SUFFIX}"
    # PostgreSQL truncates longer names silently — possibly onto a live database.
    assert len(name.encode()) <= _POSTGRES_IDENTIFIER_BYTES, "child test database name too long"
    return base.set(database=name)


def _drop_database(url: URL) -> None:
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    finally:
        admin.dispose()


def _junit_totals(report: Path) -> dict[str, int]:
    if not report.exists():
        return {}
    root = ElementTree.parse(report).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    return {key: int(suite.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}


def _run_operator_tests_under(directory: Path) -> tuple[int, str, dict[str, int]]:
    """The operator suite in a child pytest whose cwd — and so whose `.env` — is `directory`.

    Everything that holds a database URL lives in this frame, not the test's, so
    a failing assertion (even under --showlocals) prints no credentials.
    """
    child_database = _child_database_url()
    report = directory / "operator-tests.xml"
    _drop_database(child_database)  # reclaim one left by a run that was killed
    try:
        returncode, output = _run(
            [
                sys.executable, "-m", "pytest", str(OPERATOR_API_TESTS),
                "-c", str(API_ROOT / "pytest.ini"),
                "--rootdir", str(API_ROOT),
                "-p", "no:cacheprovider",
                "-rfE",
                "--junitxml", str(report),
            ],
            cwd=directory,
            environment=_child_environment(
                DATABASE_URL=settings.DATABASE_URL,
                TEST_DATABASE_URL=child_database.render_as_string(hide_password=False),
            ),
            timeout=600,
        )
    finally:
        _drop_database(child_database)
    return returncode, output, _junit_totals(report)


def _operator_test_function_count() -> int:
    tree = ast.parse(OPERATOR_API_TESTS.read_text(encoding="utf-8"))
    return sum(
        isinstance(node, ast.FunctionDef) and node.name.startswith("test_") for node in tree.body
    )


# --- the suite does not depend on the developer's secret ---------------------------


def test_operator_tests_pass_when_a_developer_env_contradicts_the_posture(tmp_path):
    """The CI-level guard: the whole operator suite, under a developer `.env` with a token.

    The fake `.env` sets a token AND disables the API, so removing either half of
    the conftest pin fails this (403s without the token pin, 404s without the
    enabled pin). The positive control first establishes that the child really
    loads that file — otherwise a green child would prove nothing.
    """
    _write_fake_developer_env(tmp_path)
    assert _application_settings_load_the_fake_env(tmp_path), (
        "the child's settings did not load the fake developer .env; the run below would prove nothing"
    )

    returncode, output, totals = _run_operator_tests_under(tmp_path)

    assert FAKE_DEVELOPER_TOKEN not in output
    assert returncode == 0, output[-4000:]
    # Every operator test ran and passed: none skipped, xfailed or deselected away.
    assert totals.get("tests", 0) >= _operator_test_function_count(), totals
    assert (totals.get("failures"), totals.get("errors"), totals.get("skipped")) == (0, 0, 0), totals


def test_the_suite_runs_with_the_operator_boundary_in_its_test_posture():
    """The in-process view of the pin.

    The token is pinned to the suite's own public one, never None: the boundary
    fails closed without a token, so None would be a suite of 403s. Compared
    into booleans so a failure prints no secret.
    """
    token_is_pinned = settings.OPERATOR_API_TOKEN == OPERATOR_TEST_TOKEN
    enabled_is_pinned = settings.OPERATOR_API_ENABLED is True

    assert token_is_pinned, "OPERATOR_API_TOKEN from the environment reached the test process"
    assert enabled_is_pinned, "OPERATOR_API_ENABLED from the environment reached the test process"


# --- production configuration is unchanged -----------------------------------------


def test_the_application_settings_still_load_operator_config_from_env(tmp_path):
    """The module-level `settings` that `require_operator` reads, built as production builds it.

    Not the `Settings` class with an explicit file: an edit to config.py that
    ignored the configured token (`Settings(OPERATOR_API_TOKEN=None)`, or not
    reading `.env`) would leave the class tests below green and the operator
    boundary silently open. It would fail here.
    """
    _write_fake_developer_env(tmp_path)
    assert _application_settings_load_the_fake_env(tmp_path)


def test_production_loading_still_reads_the_token_from_the_env_file(
    tmp_path, no_operator_environment
):
    path = tmp_path / ".env"
    path.write_text(
        f"DATABASE_URL={DUMMY_DATABASE_URL}\n"
        "OPERATOR_API_TOKEN=from-the-env-file\nOPERATOR_API_ENABLED=false\n",
        encoding="utf-8",
    )

    loaded = Settings(_env_file=path)
    token_from_file = loaded.OPERATOR_API_TOKEN == "from-the-env-file"
    disabled_from_file = loaded.OPERATOR_API_ENABLED is False

    assert token_from_file
    assert disabled_from_file


def test_an_environment_variable_still_wins_over_the_env_file(
    tmp_path, monkeypatch, no_operator_environment
):
    path = tmp_path / ".env"
    path.write_text(
        f"DATABASE_URL={DUMMY_DATABASE_URL}\nOPERATOR_API_TOKEN=from-the-env-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPERATOR_API_TOKEN", "from-the-environment")

    environment_wins = Settings(_env_file=path).OPERATOR_API_TOKEN == "from-the-environment"
    assert environment_wins


def test_the_production_defaults_are_unchanged(monkeypatch, no_operator_environment):
    monkeypatch.setenv("DATABASE_URL", DUMMY_DATABASE_URL)

    loaded = Settings(_env_file=None)
    no_token_by_default = loaded.OPERATOR_API_TOKEN is None

    assert no_token_by_default
    assert loaded.OPERATOR_API_ENABLED is True
    assert Settings.model_config.get("env_file") == ".env"
