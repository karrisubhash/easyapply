"""The production identity path: the web app's assertion of an Entra sign-in.

Three things are proved here, against the real routes, database and migrations:

  1. ONLY a valid assertion is an identity. Every way an assertion can be
     wrong — missing, malformed, unsigned, signed with another key or
     algorithm, expired, early, too long-lived, for another issuer, audience,
     identity provider, tenant or subject shape, replayed, or accompanied by the
     development header — is the same 401, and none of them provisions a user
     or echoes what it was sent.
  2. The account is the external identity, never the email. First sight makes
     one user; the same person again is the same user even with a new email;
     two people with one email are two users; concurrent first sights make one.
  3. The domain behaves exactly as it always has under this identity source:
     a full A-and-B matrix over every user-owned resource, in both directions,
     where every foreign id answers byte-for-byte what a never-issued id does.
"""

from __future__ import annotations

import logging
import secrets as random_tokens
import threading
import time

import jwt
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.api import assertion_identity
from app.api.assertion_identity import AssertionRefused, validate_assertion
from app.core.config import Settings, identity_configuration_problems, settings
from app.models import User, UserIdentity
from tests.job_fixtures import make_job_payload
from tests.resume_fixtures import make_resume_pdf

TENANT = "11111111-2222-4333-8444-555555555555"
ISSUER = f"https://{TENANT}.ciamlogin.com/{TENANT}/v2.0"
SECRET = "assertion-secret-for-tests-only-0123456789"
PREVIOUS = "assertion-secret-rotated-out-0123456789abc"
OID_A = "0a0a0a0a-1111-4222-8333-444444444444"
OID_B = "0b0b0b0b-1111-4222-8333-444444444444"
PROFILE = "/api/v1/profile"
DROP = object()


def mint(sub: str = OID_A, *, secret: str = SECRET, lifetime: int = 60, now: int | None = None, algorithm: str = "HS256", **overrides) -> str:
    now = int(time.time()) if now is None else now
    tag = sub[:4] if isinstance(sub, str) else "none"
    claims = {
        "iss": "easyapply-web",
        "aud": "easyapply-api",
        "sub": sub,
        "idp": ISSUER,
        "tid": TENANT,
        "email": f"{tag}@example.test",
        "name": f"Person {tag}",
        "sid": "session-1",
        "iat": now,
        "nbf": now,
        "exp": now + lifetime,
        "jti": random_tokens.token_urlsafe(24),
    }
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not DROP}
    return jwt.encode(claims, secret, algorithm=algorithm)


def bearer(sub: str = OID_A, **overrides) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(sub, **overrides)}"}


@pytest.fixture
def entra(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "entra")
    monkeypatch.setattr(settings, "IDENTITY_ASSERTION_SECRET", SECRET)
    monkeypatch.setattr(settings, "IDENTITY_ASSERTION_SECRET_PREVIOUS", "")
    monkeypatch.setattr(settings, "ENTRA_TENANT_ID", TENANT)
    monkeypatch.setattr(settings, "ENTRA_ISSUER", ISSUER)
    assertion_identity.replay_guard.clear()
    yield
    assertion_identity.replay_guard.clear()


def user_count(db) -> int:
    return db.scalar(select(func.count()).select_from(User))


def assert_refused(response) -> None:
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "The request did not carry a valid EasyApply sign-in.", "code": "unauthenticated"}


# --- 1. only a valid assertion is an identity --------------------------------------------


def test_a_valid_assertion_is_the_caller(client, db, entra):
    response = client.get(PROFILE, headers=bearer())
    # No profile yet: an answer about a RESOLVED user.
    assert response.status_code == 404
    mapping = db.scalar(select(UserIdentity).where(UserIdentity.subject == OID_A))
    assert mapping is not None
    # The canonical tenant key, not the configured issuer URL.
    assert mapping.issuer == f"entra:{TENANT}"
    assert mapping.email == "0a0a@example.test"
    assert db.get(User, mapping.user_id).email is None, "a real sign-in never gets a development email key"


REJECTIONS = {
    "missing": lambda: None,
    "not bearer": lambda: f"Basic {mint()}",
    "bearer without token": lambda: "Bearer",
    "two tokens": lambda: f"Bearer {mint()} {mint()}",
    "garbage": lambda: "Bearer not.a.jwt",
    "oversized": lambda: "Bearer " + "a" * 5000,
    "unsigned (alg none)": lambda: "Bearer " + jwt.encode({"sub": OID_A}, None, algorithm="none"),
    "HS512": lambda: f"Bearer {mint(algorithm='HS512')}",
    "another secret": lambda: f"Bearer {mint(secret='x' * 40)}",
    "expired": lambda: f"Bearer {mint(now=int(time.time()) - 3600)}",
    "not yet valid": lambda: f"Bearer {mint(now=int(time.time()) + 600)}",
    "too long-lived": lambda: f"Bearer {mint(lifetime=3600)}",
    "wrong issuer": lambda: f"Bearer {mint(iss='somebody-else')}",
    "wrong audience": lambda: f"Bearer {mint(aud='another-api')}",
    "wrong identity provider": lambda: f"Bearer {mint(idp='https://evil.ciamlogin.com/x/v2.0')}",
    "wrong tenant": lambda: f"Bearer {mint(tid='99999999-2222-4333-8444-555555555555')}",
    "subject not an object id": lambda: f"Bearer {mint(sub='victim@example.com')}",
    "no subject": lambda: f"Bearer {mint(sub=DROP)}",
    "no jti": lambda: f"Bearer {mint(jti=DROP)}",
    "no expiry": lambda: f"Bearer {mint(exp=DROP)}",
}


@pytest.mark.parametrize("case", sorted(REJECTIONS))
def test_every_invalid_assertion_is_the_same_401_and_provisions_nobody(client, db, entra, case):
    before = user_count(db)
    authorization = REJECTIONS[case]()
    headers = {} if authorization is None else {"Authorization": authorization}
    assert_refused(client.get(PROFILE, headers=headers))
    assert_refused(client.post(PROFILE, headers=headers))
    assert user_count(db) == before


def test_an_rs256_header_over_an_hmac_signature_is_refused(entra):
    token = mint()
    header, payload, signature = token.split(".")
    forged_header = jwt.utils.base64url_encode(b'{"alg":"RS256","typ":"JWT"}').decode()
    with pytest.raises(AssertionRefused) as refused:
        validate_assertion(f"Bearer {forged_header}.{payload}.{signature}")
    assert refused.value.reason == "bad_algorithm"


def test_an_assertion_is_good_for_one_request(client, entra):
    headers = bearer()
    assert client.get(PROFILE, headers=headers).status_code == 404
    assert_refused(client.get(PROFILE, headers=headers))


def test_the_development_header_is_refused_not_ignored(client, db, entra):
    before = user_count(db)
    assert_refused(client.get(PROFILE, headers={**bearer(), "X-Dev-User-Email": "victim@example.com"}))
    assert_refused(client.get(PROFILE, headers={"X-Dev-User-Email": "victim@example.com"}))
    assert user_count(db) == before


@pytest.mark.parametrize(
    "header",
    ["X-User-Id", "X-User-Email", "X-Forwarded-User", "X-Ms-Client-Principal-Id", "X-Remote-User"],
)
def test_other_identity_shaped_headers_change_nothing(client, db, entra, header):
    client.post(PROFILE, headers=bearer(OID_A))
    response = client.get(PROFILE, headers={**bearer(OID_B), header: "1"})
    # B has no profile, whatever the header claims about A.
    assert response.status_code == 404


def test_the_previous_secret_is_accepted_during_a_rotation_and_not_after(client, entra, monkeypatch):
    monkeypatch.setattr(settings, "IDENTITY_ASSERTION_SECRET_PREVIOUS", PREVIOUS)
    assert client.get(PROFILE, headers=bearer(secret=PREVIOUS)).status_code == 404
    monkeypatch.setattr(settings, "IDENTITY_ASSERTION_SECRET_PREVIOUS", "")
    assert_refused(client.get(PROFILE, headers=bearer(secret=PREVIOUS)))


def test_a_short_secret_fails_closed_per_request(client, entra, monkeypatch):
    monkeypatch.setattr(settings, "IDENTITY_ASSERTION_SECRET", "short")
    assert_refused(client.get(PROFILE, headers=bearer(secret="short" * 8)))


def test_refusals_are_logged_by_category_and_never_log_the_token(client, entra, caplog, monkeypatch):
    token = mint(secret="y" * 40)
    # The session's `alembic upgrade` runs `fileConfig`, which disables every
    # logger that already existed — this one included.
    monkeypatch.setattr(assertion_identity.logger, "disabled", False)
    with caplog.at_level(logging.DEBUG, logger=assertion_identity.logger.name):
        assert_refused(client.get(PROFILE, headers={"Authorization": f"Bearer {token}"}))
    reasons = [getattr(record, "reason", None) for record in caplog.records if record.getMessage() == "identity.refused"]
    assert reasons == ["bad_signature"]
    logged = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert token not in logged
    assert token.split(".")[1] not in logged


def test_production_accepts_the_assertion(client, entra, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert client.get(PROFILE, headers=bearer()).status_code == 404
    assert_refused(client.get(PROFILE, headers={"X-Dev-User-Email": "dev@easyapply.local"}))


def test_production_without_entra_still_refuses_everything(client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    response = client.get(PROFILE, headers={"X-Dev-User-Email": "dev@easyapply.local"})
    assert response.status_code == 401


# --- configuration ------------------------------------------------------------------------


def configured(**values) -> Settings:
    base = {
        "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/nowhere",
        "AUTH_MODE": "entra",
        "IDENTITY_ASSERTION_SECRET": SECRET,
        "ENTRA_TENANT_ID": TENANT,
        "ENTRA_ISSUER": ISSUER,
    }
    base.update(values)
    return Settings(_env_file=None, **base)


def test_a_complete_entra_configuration_has_no_problems():
    assert identity_configuration_problems(configured()) == []


@pytest.mark.parametrize(
    ("values", "problem"),
    [
        ({"IDENTITY_ASSERTION_SECRET": ""}, "IDENTITY_ASSERTION_SECRET must be at least 32 characters"),
        ({"IDENTITY_ASSERTION_SECRET_PREVIOUS": "short"}, "IDENTITY_ASSERTION_SECRET_PREVIOUS"),
        ({"ENTRA_TENANT_ID": "contoso"}, "ENTRA_TENANT_ID must be the tenant's GUID"),
        ({"ENTRA_ISSUER": "http://evil.example/x/v2.0"}, "ENTRA_ISSUER must be the tenant's https token issuer"),
        ({"ENTRA_ISSUER": "https://other.ciamlogin.com/other/v2.0"}, "ENTRA_ISSUER must name ENTRA_TENANT_ID"),
        (
            {"ENVIRONMENT": "production", "ENTRA_ISSUER": f"http://127.0.0.1:3190/{TENANT}/v2.0"},
            "ENTRA_ISSUER cannot be a loopback address in production",
        ),
    ],
)
def test_an_incomplete_entra_configuration_is_named_without_its_values(values, problem):
    problems = identity_configuration_problems(configured(**values))
    assert any(problem in item for item in problems), problems
    assert SECRET not in " ".join(problems)


def test_development_mode_needs_nothing():
    assert identity_configuration_problems(configured(AUTH_MODE="development", IDENTITY_ASSERTION_SECRET="")) == []


def test_the_auth_mode_is_a_closed_set():
    with pytest.raises(Exception) as error:
        configured(AUTH_MODE="entraa")
    assert SECRET not in str(error.value)


def test_serving_refuses_a_broken_configuration(monkeypatch):
    """The lifespan runs the check before anything starts, so uvicorn exits."""
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(settings, "AUTH_MODE", "entra")
    monkeypatch.setattr(settings, "IDENTITY_ASSERTION_SECRET", "")
    with pytest.raises(RuntimeError, match="IDENTITY_ASSERTION_SECRET"):
        with TestClient(main.app):
            pass


def test_a_broken_configuration_refuses_every_request_even_without_the_lifespan(client, entra, monkeypatch):
    monkeypatch.setattr(settings, "ENTRA_TENANT_ID", "")
    response = client.get(PROFILE, headers=bearer(OID_A))
    assert response.status_code == 401


def test_the_api_names_the_web_variable_for_dev_session():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="mode of the web app"):
        Settings(AUTH_MODE="dev-session", _env_file=None)


def test_a_development_api_refuses_a_sign_in_assertion(client, db):
    """Web in entra mode, API left in development: never fall back to DEV_USER_EMAIL."""
    response = client.get(PROFILE, headers={"Authorization": "Bearer anything"})
    assert response.status_code == 401
    assert "does not accept sign-in assertions" in response.text
    # ...and a well-formed one is refused the same way: the mode, not the token, decides.
    response = client.get(PROFILE, headers=bearer(OID_A))
    assert response.status_code == 401
    assert db.scalar(select(UserIdentity)) is None


def test_startup_refuses_a_broken_configuration(monkeypatch):
    from app import main

    monkeypatch.setattr(settings, "AUTH_MODE", "entra")
    monkeypatch.setattr(settings, "IDENTITY_ASSERTION_SECRET", "")
    with pytest.raises(RuntimeError, match="IDENTITY_ASSERTION_SECRET"):
        main.check_identity_configuration()


# --- 2. the account is the external identity -----------------------------------------------


def test_the_same_person_is_the_same_account_even_with_a_new_email(client, db, entra):
    assert client.post(PROFILE, headers=bearer(email="old@example.test")).status_code == 201
    first = db.scalar(select(UserIdentity).where(UserIdentity.subject == OID_A))
    assert client.get(PROFILE, headers=bearer(email="new@example.test", name="Renamed")).status_code == 200
    db.expire_all()
    again = db.scalar(select(UserIdentity).where(UserIdentity.subject == OID_A))
    assert again.user_id == first.user_id
    assert (again.email, again.display_name) == ("new@example.test", "Renamed")
    assert db.scalar(select(func.count()).select_from(UserIdentity).where(UserIdentity.subject == OID_A)) == 1


def test_two_people_with_one_email_are_two_accounts(client, db, entra):
    shared = "shared@example.test"
    client.post(PROFILE, headers=bearer(OID_A, email=shared))
    assert client.get(PROFILE, headers=bearer(OID_B, email=shared)).status_code == 404, "B must not see A's profile"
    users = db.scalars(select(UserIdentity.user_id).where(UserIdentity.subject.in_([OID_A, OID_B]))).all()
    assert len(set(users)) == 2


def test_an_email_matching_a_development_user_is_not_linked_to_it(client, db, entra, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "development")
    assert client.post(PROFILE, headers={"X-Dev-User-Email": "taken@example.test"}).status_code == 201
    dev_user = db.scalar(select(User).where(User.email == "taken@example.test"))
    monkeypatch.setattr(settings, "AUTH_MODE", "entra")
    assert client.get(PROFILE, headers=bearer(OID_A, email="taken@example.test")).status_code == 404
    mapping = db.scalar(select(UserIdentity).where(UserIdentity.subject == OID_A))
    assert mapping.user_id != dev_user.id


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("Ali" + chr(0x200C) + "reza", "Ali" + chr(0x200C) + "reza"),  # ZWNJ, common in Persian names
        ("Ada" + chr(0xA0) + "Lovelace", "Ada" + chr(0xA0) + "Lovelace"),  # non-breaking space
        ("Grace " + chr(0x1F469) + chr(0x200D) + chr(0x1F4BB), "Grace " + chr(0x1F469) + chr(0x200D) + chr(0x1F4BB)),  # emoji sequence
        ("N" * 256, "N" * 255),  # Entra allows 256; cut to the column
        ("Bob" + chr(0) + chr(7), "Bob"),  # control characters dropped
        ("   ", None),
        (12345, None),
    ],
)
def test_display_metadata_never_refuses_a_valid_sign_in(client, db, entra, name, stored):
    response = client.post(PROFILE, headers=bearer(OID_A, name=name, email="x" + chr(0) + "@example.test"))
    assert response.status_code == 201, response.text
    mapping = db.scalar(select(UserIdentity).where(UserIdentity.subject == OID_A))
    assert mapping.display_name == stored
    assert mapping.email == "x@example.test"


def test_reconfiguring_the_issuer_url_keeps_every_account(client, db, entra, monkeypatch):
    """Accounts are keyed by the tenant, not by the issuer string an operator can edit."""
    client.post(PROFILE, headers=bearer(OID_A))
    corrected = ISSUER + "/"
    monkeypatch.setattr(settings, "ENTRA_ISSUER", corrected)
    assert client.get(PROFILE, headers=bearer(OID_A, idp=corrected)).status_code == 200


def test_a_full_replay_guard_refuses_rather_than_forgetting_an_unexpired_id():
    guard = assertion_identity._ReplayGuard(capacity=2)
    now = 1_000.0
    assert guard.first_use("a" * 16, now + 60, now)
    assert guard.first_use("b" * 16, now + 60, now)
    with pytest.raises(assertion_identity.AssertionRefused) as refused:
        guard.first_use("c" * 16, now + 60, now)
    assert refused.value.reason == "replay_guard_full"
    # Nothing was forgotten: the first is still refused as a replay...
    assert guard.first_use("a" * 16, now + 60, now) is False
    # ...and once the held ids expire there is room again.
    assert guard.first_use("c" * 16, now + 200, now + 100)


def test_a_lone_surrogate_in_metadata_is_replaced_not_a_failure(client, db, entra):
    response = client.post(PROFILE, headers=bearer(OID_A, name="Ada" + chr(0xD800), email="x" + chr(0xDC00) + "@example.test"))
    assert response.status_code == 201, response.text
    mapping = db.scalar(select(UserIdentity).where(UserIdentity.subject == OID_A))
    assert mapping.display_name == "Ada" + chr(0xFFFD)
    assert mapping.email == "x" + chr(0xFFFD) + "@example.test"


def test_the_subject_is_case_insensitive_as_a_guid(client, db, entra):
    client.post(PROFILE, headers=bearer(OID_A))
    assert client.get(PROFILE, headers=bearer(OID_A.upper())).status_code == 200


RACE_OID = "0c0c0c0c-1111-4222-8333-444444444444"


@pytest.fixture
def race_identities(engine):
    yield
    with engine.begin() as connection:
        ids = connection.execute(text("SELECT user_id FROM user_identities WHERE subject = :s"), {"s": RACE_OID}).scalars().all()
        connection.execute(text("DELETE FROM user_identities WHERE subject = :s"), {"s": RACE_OID})
        if ids:
            connection.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": list(ids)})


def test_simultaneous_first_sign_ins_make_one_account(engine, entra, race_identities):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    workers = 8
    barrier = threading.Barrier(workers)
    resolved: list[int] = []
    errors: list[BaseException] = []
    tokens = [mint(RACE_OID) for _ in range(workers)]

    def first_sight(token: str) -> None:
        db = factory()
        try:
            barrier.wait(timeout=10)
            user = assertion_identity.asserted_user(db, authorization=f"Bearer {token}", x_dev_user_email=None)
            resolved.append(user.id)
        except BaseException as exc:  # asserted below
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=first_sight, args=(token,)) for token in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(resolved) == workers and len(set(resolved)) == 1
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM user_identities WHERE subject = :s"), {"s": RACE_OID}).scalar_one() == 1


# --- 3. the whole domain, two people, both directions ------------------------------------------

PDF = "application/pdf"


def build_world(client, queue, tailoring_queue, oid: str, tag: str) -> dict:
    """Everything one person owns, made through the real routes as that person."""

    def as_me():
        return bearer(oid)

    upload = client.post("/api/v1/resumes", headers=as_me(), files={"file": (f"{tag}.pdf", make_resume_pdf(), PDF)})
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["id"]
    queue.drain()
    result = client.get(f"/api/v1/resumes/{document_id}/result", headers=as_me())
    assert result.status_code == 200, result.text
    number = result.json()["result"]["profile_version_number"]
    snapshot = client.get(f"/api/v1/profile/versions/{number}", headers=as_me()).json()["snapshot"]
    snapshot["experiences"] = [
        {
            "company": f"{tag} Corp",
            "job_title": "Engineer",
            "start_date": "2020-01-01",
            "start_date_precision": "month",
            "description": "Built Python services on PostgreSQL.",
        }
    ]
    snapshot["skills"] = [{"name": "Python"}, {"name": "PostgreSQL"}]
    assert client.put(f"/api/v1/profile/versions/{number}/snapshot", headers=as_me(), json=snapshot).status_code == 200
    confirmed = client.post(f"/api/v1/profile/versions/{number}/confirm", headers=as_me(), json={})
    assert confirmed.status_code == 200, confirmed.text
    job = client.post(
        "/api/v1/jobs",
        headers=as_me(),
        json=make_job_payload(
            external_job_id=f"entra-{tag}",
            description_text="Requirements:\n- Strong proficiency in Python\n- Experience with PostgreSQL\n",
        ),
    )
    assert job.status_code == 201, job.text
    job_id = job.json()["id"]
    match = client.post(f"/api/v1/jobs/{job_id}/matches", headers=as_me(), json={})
    assert match.status_code == 201, match.text
    started = client.post(f"/api/v1/jobs/{job_id}/tailored-resumes", headers=as_me(), json={"match_result_id": match.json()["id"]})
    assert started.status_code == 201, started.text
    tailoring_queue.drain()
    run_id = started.json()["id"]
    run = client.get(f"/api/v1/tailored-resumes/{run_id}", headers=as_me()).json()
    assert run["status"] == "completed" and run["has_artifact"], run
    assert client.get(f"/api/v1/tailored-resumes/{run_id}/artifact", headers=as_me()).status_code == 200
    return {
        "document": document_id,
        "version_number": number,
        "version_id": confirmed.json()["id"],
        "job": job_id,
        "match": match.json()["id"],
        "run": run_id,
    }


MISSING = 987_654_321


def foreign_requests(world: dict) -> list[tuple[str, str, dict | None, str]]:
    """(method, path to the owner's resource, json body, the same path with a never-issued id)."""
    d, j, m, r = world["document"], world["job"], world["match"], world["run"]
    return [
        ("GET", f"/api/v1/resumes/{d}", None, f"/api/v1/resumes/{MISSING}"),
        ("GET", f"/api/v1/resumes/{d}/result", None, f"/api/v1/resumes/{MISSING}/result"),
        ("POST", f"/api/v1/resumes/{d}/reprocess", None, f"/api/v1/resumes/{MISSING}/reprocess"),
        ("GET", f"/api/v1/jobs/{j}", None, f"/api/v1/jobs/{MISSING}"),
        ("GET", f"/api/v1/jobs/{j}/requirements", None, f"/api/v1/jobs/{MISSING}/requirements"),
        ("POST", f"/api/v1/jobs/{j}/reprocess", None, f"/api/v1/jobs/{MISSING}/reprocess"),
        ("GET", f"/api/v1/jobs/{j}/matches", None, f"/api/v1/jobs/{MISSING}/matches"),
        ("POST", f"/api/v1/jobs/{j}/matches", {}, f"/api/v1/jobs/{MISSING}/matches"),
        ("GET", f"/api/v1/jobs/{j}/tailored-resumes", None, f"/api/v1/jobs/{MISSING}/tailored-resumes"),
        ("POST", f"/api/v1/jobs/{j}/tailored-resumes", {}, f"/api/v1/jobs/{MISSING}/tailored-resumes"),
        ("GET", f"/api/v1/matches/{m}", None, f"/api/v1/matches/{MISSING}"),
        ("GET", f"/api/v1/tailored-resumes/{r}", None, f"/api/v1/tailored-resumes/{MISSING}"),
        ("GET", f"/api/v1/tailored-resumes/{r}/artifact", None, f"/api/v1/tailored-resumes/{MISSING}/artifact"),
    ]


def call(client, method: str, path: str, body, headers):
    return client.request(method, path, headers=headers, json=body)


def assert_isolated(client, owner: dict, stranger_oid: str, stranger_world: dict | None) -> None:
    for method, path, body, missing in foreign_requests(owner):
        foreign = call(client, method, path, body, bearer(stranger_oid))
        never = call(client, method, missing, body, bearer(stranger_oid))
        assert foreign.status_code == 404, f"{method} {path}: {foreign.status_code} {foreign.text}"
        assert foreign.json() == never.json(), f"{method} {path} is distinguishable from a missing id"
        assert "content-disposition" not in foreign.headers

    # The stranger's own listings contain none of the owner's rows.
    jobs = client.get("/api/v1/jobs", headers=bearer(stranger_oid)).json()
    assert owner["job"] not in [job["id"] for job in jobs]
    runs = client.get("/api/v1/tailored-resumes", headers=bearer(stranger_oid)).json()
    assert owner["run"] not in [run["id"] for run in runs]
    resumes = client.get("/api/v1/resumes", headers=bearer(stranger_oid)).json()
    assert owner["document"] not in [resume["id"] for resume in resumes]

    # The owner's ids named inside the stranger's OWN requests are refused like missing ones.
    if stranger_world:
        foreign_version = client.post(
            f"/api/v1/jobs/{stranger_world['job']}/matches",
            headers=bearer(stranger_oid),
            json={"profile_version_id": owner["version_id"]},
        )
        assert foreign_version.status_code == 404
        foreign_match = client.post(
            f"/api/v1/jobs/{stranger_world['job']}/tailored-resumes",
            headers=bearer(stranger_oid),
            json={"match_result_id": owner["match"]},
        )
        assert foreign_match.status_code == 404
        # The stranger's profile versions are theirs: version N is their own N, never the owner's.
        own = client.get(f"/api/v1/profile/versions/{owner['version_number']}", headers=bearer(stranger_oid))
        if own.status_code == 200:
            assert own.json()["id"] != owner["version_id"]
        versions = client.get("/api/v1/profile/versions", headers=bearer(stranger_oid)).json()
        assert owner["version_id"] not in [version["id"] for version in versions]
        current = client.get("/api/v1/profile/versions/current", headers=bearer(stranger_oid))
        assert current.json().get("id") != owner["version_id"]


def test_every_resource_is_isolated_in_both_directions(client, queue, tailoring_queue, entra, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "RESUME_STORAGE_ROOT", str(tmp_path))
    world_a = build_world(client, queue, tailoring_queue, OID_A, "alpha")

    # B, with nothing yet: every one of A's resources is a 404 like a missing one.
    assert_isolated(client, world_a, OID_B, None)
    # B cannot write into A's profile versions: B has no profile to address them in.
    assert client.put(
        f"/api/v1/profile/versions/{world_a['version_number']}/snapshot", headers=bearer(OID_B), json={"experiences": [], "education": [], "skills": [], "projects": [], "certifications": [], "links": []}
    ).status_code == 404
    assert client.post(f"/api/v1/profile/versions/{world_a['version_number']}/confirm", headers=bearer(OID_B), json={}).status_code == 404

    # B builds a world of their own; then both directions.
    world_b = build_world(client, queue, tailoring_queue, OID_B, "bravo")
    assert_isolated(client, world_a, OID_B, world_b)
    assert_isolated(client, world_b, OID_A, world_a)

    # And each still sees their own.
    assert client.get(f"/api/v1/matches/{world_a['match']}", headers=bearer(OID_A)).status_code == 200
    assert client.get(f"/api/v1/matches/{world_b['match']}", headers=bearer(OID_B)).status_code == 200


# --- operators ---------------------------------------------------------------------------

ATTENTION = "/api/v1/internal/resume-processing/attention"


def operator_headers(**assertion):
    from tests.conftest import OPERATOR_TEST_TOKEN

    return {"X-Operator-Token": OPERATOR_TEST_TOKEN, **bearer(OID_A, **assertion)}


def test_an_operator_needs_the_token_and_the_role(client, entra):
    assert client.get(ATTENTION, headers=operator_headers(roles=["EasyApply.Operator"])).status_code == 200


@pytest.mark.parametrize(
    "assertion",
    [
        {},  # signed in, no role
        {"roles": ["EasyApply.Reader"]},
        {"roles": "EasyApply.Operator"},  # a string, not a list: no roles at all
        {"roles": ["EasyApply.Operator"], "secret": "z" * 40},  # the role, in a forged assertion
    ],
)
def test_the_token_alone_does_not_open_the_operator_surface(client, entra, assertion):
    assert client.get(ATTENTION, headers=operator_headers(**assertion)).status_code == 403


def test_the_role_without_the_token_does_not_either(client, entra):
    assert client.get(ATTENTION, headers=bearer(OID_A, roles=["EasyApply.Operator"])).status_code == 403


@pytest.mark.parametrize("headers", [{}, {"roles": None}, {"roles": ["EasyApply.Operator"]}])
def test_real_sign_in_never_leaves_the_operator_surface_open_in_development(client, entra, monkeypatch, headers):
    """ENVIRONMENT=development with no token used to mean "open" — never under entra."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "OPERATOR_API_TOKEN", None)
    sent = {} if not headers else bearer(OID_A, **({"roles": headers["roles"]} if headers["roles"] else {}))
    assert client.get(ATTENTION, headers=sent).status_code == 403


def test_a_development_api_refuses_an_assertion_on_the_operator_surface_too(client):
    from tests.conftest import OPERATOR_TEST_TOKEN

    response = client.get(ATTENTION, headers={"X-Operator-Token": OPERATOR_TEST_TOKEN, "Authorization": "Bearer x"})
    assert response.status_code == 403
    # ...while the token alone still opens it in development mode, as before.
    assert client.get(ATTENTION, headers={"X-Operator-Token": OPERATOR_TEST_TOKEN}).status_code == 200
