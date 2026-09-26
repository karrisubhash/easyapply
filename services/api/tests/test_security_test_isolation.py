"""The suite cannot reach a paid AI provider, whatever the developer's `.env` says.

`settings` is built at import from the `.env` in the working directory. A
developer who turned an AI flag on locally, key beside it, used to have the
suite send resumes to a model and bill for it — `tests/conftest.py` pinned the
queue backends and the operator posture but none of the AI switches.

Two tests, the same shape as `test_operator_test_isolation.py`:

* in process: this session has every switch off, the rollout list empty, every
  key blank and no provider secret in the environment;
* in a child pytest whose only `.env` turns everything ON and supplies fake keys
  (plus one fake key in the process environment): the same in-process test still
  passes, after a positive control shows that the child really loaded the file.
  Without that child, the in-process test is vacuous on any machine whose
  defaults already agree — which is every CI machine.

Every comparison that could see a key value is made into a boolean first, and
the child's output is checked for the fake keys: pytest prints both sides of a
failed comparison.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.core.config import Settings, settings
from tests.conftest import AI_SWITCHES, PROVIDER_KEYS, _is_provider_secret

API_ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()
IN_PROCESS_TEST = "test_the_session_starts_with_every_ai_switch_off_and_no_key"

DUMMY_DATABASE_URL = "postgresql+psycopg://u:p@localhost:5432/nowhere"
FAKE_FILE_KEY = "fake-developer-key-from-env-file"
FAKE_ENVIRONMENT_KEY = "fake-developer-key-from-environment"

_FAKE_DEVELOPER_ENV = (
    "RESUME_AI_ENABLED=true\n"
    "JOB_AI_ENABLED=true\n"
    "TAILORING_AI_ENABLED=true\n"
    "TAILORING_AI_USER_IDS=1,2,3\n"
    f"ANTHROPIC_API_KEY={FAKE_FILE_KEY}\n"
    f"OPENROUTER_API_KEY={FAKE_FILE_KEY}\n"
    "ENVIRONMENT=production\n"
)

#: The application's own `settings`, exactly as production builds it, reporting
#: through the exit code only — never a value.
_POSITIVE_CONTROL = (
    "from app.core.config import settings as s\n"
    "ok = (s.RESUME_AI_ENABLED and s.JOB_AI_ENABLED and s.TAILORING_AI_ENABLED\n"
    "      and s.TAILORING_AI_USER_IDS == '1,2,3' and bool(s.ANTHROPIC_API_KEY)\n"
    "      and bool(s.OPENROUTER_API_KEY) and s.ENVIRONMENT == 'production')\n"
    "raise SystemExit(0 if ok else 1)\n"
)


def _fields_ending(suffix: str) -> list[str]:
    return [name for name in Settings.model_fields if name.endswith(suffix)]


def test_the_session_starts_with_every_ai_switch_off_and_no_key():
    # The named switches exist, so the suffix sweep below is not vacuous and a
    # rename cannot shrink it unnoticed.
    assert set(AI_SWITCHES) <= set(_fields_ending("_AI_ENABLED"))
    assert set(PROVIDER_KEYS) <= set(_fields_ending("_API_KEY"))

    switched_on = [name for name in _fields_ending("_AI_ENABLED") if getattr(settings, name)]
    keyed = [name for name in _fields_ending("_API_KEY") if getattr(settings, name)]
    rollout_is_empty = settings.TAILORING_AI_USER_IDS == ""
    secrets_in_environment = [name for name in os.environ if _is_provider_secret(name)]

    assert switched_on == [], "an AI switch reached the test process switched on"
    assert keyed == [], "a provider key reached the test process"
    assert rollout_is_empty, "TAILORING_AI_USER_IDS reached the test process"
    assert secrets_in_environment == [], "a provider secret is in the process environment"
    assert settings.ENVIRONMENT == "test"


def _child_environment(**overrides: str) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("PYTEST_") and not _is_provider_secret(name)
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONPATH"] = str(API_ROOT)
    environment["DATABASE_URL"] = DUMMY_DATABASE_URL
    environment.update(overrides)
    return environment


def _run(args: list[str], *, cwd: Path, environment: dict[str, str]) -> tuple[int, str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    return completed.returncode, completed.stdout + completed.stderr


def test_a_developer_env_with_every_ai_switch_on_does_not_reach_the_suite(tmp_path):
    (tmp_path / ".env").write_text(_FAKE_DEVELOPER_ENV, encoding="utf-8")
    environment = _child_environment(OPENROUTER_API_KEY=FAKE_ENVIRONMENT_KEY)

    control, _ = _run([sys.executable, "-c", _POSITIVE_CONTROL], cwd=tmp_path, environment=environment)
    assert control == 0, "the child did not load the fake developer .env; the run below would prove nothing"

    # No database is needed: the test selected touches none, and the session
    # fixtures that pin configuration do not depend on one.
    returncode, output = _run(
        [
            sys.executable, "-m", "pytest", f"{THIS_FILE}::{IN_PROCESS_TEST}",
            "-c", str(API_ROOT / "pytest.ini"),
            "--rootdir", str(API_ROOT),
            "-p", "no:cacheprovider",
            "-rfE",
        ],
        cwd=tmp_path,
        environment=environment,
    )

    leaked = FAKE_FILE_KEY in output or FAKE_ENVIRONMENT_KEY in output
    assert not leaked, "a fake key appeared in the child's output"
    assert returncode == 0, output[-4000:]
    assert "1 passed" in output, output[-4000:]
