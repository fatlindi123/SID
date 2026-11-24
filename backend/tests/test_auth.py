from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.v1.health import get_db_session
from app.core.auth_security import hash_password, token_hash, verify_password
from app.models.auth import AuthChallenge, AuthSession, User
from app.models.exploration import Exploration, ExplorationPage
from app.models.project import Project, ProjectExploration, ProjectMetadata

ORIGIN = "http://localhost:8443"


def test_registration_creates_an_unverified_account_and_signs_in_without_smtp(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.api.v1.auth.check_rate_limit", lambda *_args, **_kwargs: None)
    def unexpected_email(*_args) -> None:
        raise AssertionError("registration must not send email")
    monkeypatch.setattr("app.api.v1.auth.send_email", unexpected_email)
    assert client.app.state.settings.email_delivery_configured is False
    _, headers = _csrf(client)
    payload = {
        "full_name": "New Local User",
        "email": " New.User@Example.test ",
        "password": "seventh",
        "confirm_password": "seventh",
        "next": "/projects",
    }

    response = client.post("/api/v1/auth/register", json=payload, headers=headers)

    assert response.status_code == 201
    assert response.json()["user"]["email"] == "new.user@example.test"
    assert response.json()["user"]["email_verified"] is False
    assert response.json()["next"] == "/welcome?next=%2Fprojects"
    assert client.get("/api/v1/auth/me").json()["email"] == "new.user@example.test"
    with client.app.state.test_session_factory() as session:
        user = session.scalar(select(User).where(User.email == "new.user@example.test"))
        assert user is not None and user.password_hash != "seventh"
        assert verify_password("seventh", user.password_hash)
        assert session.scalar(select(AuthChallenge).where(AuthChallenge.user_id == user.id)) is None

    signed_out = client.post("/api/v1/auth/logout", headers={
        "Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"],
    })
    assert signed_out.status_code == 204
    _, login_headers = _csrf(client)
    bad_password = client.post("/api/v1/auth/login", json={
        "email": "NEW.USER@example.test", "password": "incorrect",
    }, headers=login_headers)
    assert bad_password.status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401

    _, login_headers = _csrf(client)
    login = client.post("/api/v1/auth/login", json={
        "email": " NEW.USER@example.test ", "password": "seventh",
    }, headers=login_headers)
    assert login.status_code == 200
    assert client.get("/api/v1/auth/me").json()["email"] == "new.user@example.test"


def test_registration_rejects_password_shorter_than_seven_characters(client: TestClient) -> None:
    _, headers = _csrf(client)
    response = client.post("/api/v1/auth/register", json={
        "full_name": "Short Password", "email": "short@example.test",
        "password": "123456", "confirm_password": "123456",
    }, headers=headers)
    assert response.status_code == 422
    with client.app.state.test_session_factory() as session:
        assert session.scalar(select(User).where(User.email == "short@example.test")) is None


def test_registration_rejects_duplicate_normalized_email(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.api.v1.auth.check_rate_limit", lambda *_args, **_kwargs: None)
    _, headers = _csrf(client)
    first = client.post("/api/v1/auth/register", json={
        "full_name": "Original Name", "email": "Owner@Example.test",
        "password": "password-seven", "confirm_password": "password-seven",
    }, headers=headers)
    assert first.status_code == 201
    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": first.json()["csrf_token"]})
    _, headers = _csrf(client)
    duplicate = client.post("/api/v1/auth/register", json={
        "full_name": "Different Name", "email": "owner@example.test",
        "password": "different-password", "confirm_password": "different-password",
    }, headers=headers)
    assert duplicate.status_code == 409
    with client.app.state.test_session_factory() as session:
        rows = session.scalars(select(User).where(User.email == "owner@example.test")).all()
        assert len(rows) == 1 and rows[0].full_name == "Original Name"
        assert verify_password("password-seven", rows[0].password_hash)


def test_registration_rejects_mismatched_password_confirmation(client: TestClient) -> None:
    _, headers = _csrf(client)
    response = client.post("/api/v1/auth/register", json={
        "full_name": "Mismatch", "email": "mismatch@example.test",
        "password": "password-seven", "confirm_password": "different-password",
    }, headers=headers)
    assert response.status_code == 422


def _csrf(client: TestClient) -> tuple[str, dict[str, str]]:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    return token, {"Origin": ORIGIN, "X-CSRF-Token": token}


def _create_user(
    client: TestClient,
    email: str,
    *,
    verified: bool = True,
    onboarding_completed: bool = False,
) -> User:
    user = User(
        email=email,
        full_name="Test User",
        password_hash=hash_password("correct-horse-battery-staple"),
        email_verified=verified,
        onboarding_completed_at=datetime.now(UTC) if onboarding_completed else None,
    )
    with client.app.state.test_session_factory.begin() as session:
        session.add(user)
    return user


def test_csrf_session_uses_httponly_server_side_cookie_and_rejects_bad_origin(client: TestClient) -> None:
    token, headers = _csrf(client)
    assert len(token) >= 32
    cookie = client.cookies.get("inscope_session")
    assert cookie and cookie != token
    set_cookie = client.cookies.jar
    assert any(item.name == "inscope_session" and item.has_nonstandard_attr("HttpOnly") for item in set_cookie)

    _create_user(client, "owner@example.test")
    response = client.post("/api/v1/auth/login", json={"email": "owner@example.test", "password": "correct-horse-battery-staple"},
                           headers={**headers, "Origin": "https://evil.example"})
    assert response.status_code == 403


def test_login_profile_update_and_logout_use_real_persisted_session(client: TestClient) -> None:
    user = _create_user(client, "owner@example.test", onboarding_completed=True)
    csrf, headers = _csrf(client)
    login = client.post("/api/v1/auth/login", json={
        "email": "OWNER@example.test", "password": "correct-horse-battery-staple", "next": "/projects?q=mine",
    }, headers=headers)
    assert login.status_code == 200
    body = login.json()
    assert body["user"] == {
        "id": str(user.id), "full_name": "Test User", "email": "owner@example.test",
        "email_verified": True, "onboarding_completed": True,
    }
    assert body["next"] == "/projects?q=mine"
    assert "password" not in str(body).lower()
    csrf = body["csrf_token"]
    assert client.get("/api/v1/auth/me").json()["email"] == "owner@example.test"

    updated = client.patch("/api/v1/auth/me", json={"full_name": "Updated Name"}, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert updated.status_code == 200
    assert updated.json()["full_name"] == "Updated Name"
    signed_out = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert signed_out.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    with client.app.state.test_session_factory() as session:
        assert session.scalar(select(AuthSession).where(AuthSession.user_id == user.id)) is None


def test_first_login_routes_to_welcome_and_completion_is_persisted_and_idempotent(client: TestClient) -> None:
    user = _create_user(client, "first-login@example.test")
    _, headers = _csrf(client)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "correct-horse-battery-staple", "next": "/projects?q=mine"},
        headers=headers,
    )
    assert login.status_code == 200
    assert login.json()["next"] == "/welcome?next=%2Fprojects%3Fq%3Dmine"
    assert login.json()["user"]["onboarding_completed"] is False
    assert client.get("/api/v1/auth/me").json()["onboarding_completed"] is False

    csrf = login.json()["csrf_token"]
    completion_headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    completed = client.post("/api/v1/auth/onboarding/complete", headers=completion_headers)
    assert completed.status_code == 200
    assert completed.json()["onboarding_completed"] is True
    with client.app.state.test_session_factory() as session:
        completed_at = session.get(User, user.id).onboarding_completed_at
        assert completed_at is not None

    repeated = client.post("/api/v1/auth/onboarding/complete", headers=completion_headers)
    assert repeated.status_code == 200
    with client.app.state.test_session_factory() as session:
        assert session.get(User, user.id).onboarding_completed_at == completed_at
    assert client.get("/api/v1/auth/me").json()["onboarding_completed"] is True


def test_onboarding_completion_requires_valid_csrf(client: TestClient) -> None:
    user = _create_user(client, "csrf-onboarding@example.test")
    _, headers = _csrf(client)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "correct-horse-battery-staple"},
        headers=headers,
    )
    assert login.status_code == 200
    rejected = client.post("/api/v1/auth/onboarding/complete", headers={"Origin": ORIGIN})
    assert rejected.status_code == 403
    assert client.get("/api/v1/auth/me").json()["onboarding_completed"] is False


def test_expired_session_is_rejected_by_profile_and_workspace_endpoints(client: TestClient) -> None:
    user = _create_user(client, "expired-session@example.test")
    _, headers = _csrf(client)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "correct-horse-battery-staple"},
        headers=headers,
    )
    assert login.status_code == 200
    raw_cookie = client.cookies.get("inscope_session")
    assert raw_cookie
    with client.app.state.test_session_factory.begin() as session:
        record = session.get(AuthSession, token_hash(raw_cookie))
        assert record is not None and record.user_id == user.id
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    client.app.dependency_overrides.pop(get_db_session, None)
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/explorations").status_code == 401


def test_production_session_cookie_is_secure_and_session_token_is_not_in_json(client: TestClient) -> None:
    user = _create_user(client, "secure-cookie@example.test")
    _, headers = _csrf(client)
    client.app.state.settings.app_environment = "production"
    response = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "correct-horse-battery-staple"},
        headers=headers,
    )
    assert response.status_code == 200
    raw_cookie = client.cookies.get("inscope_session")
    assert raw_cookie
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie
    assert raw_cookie not in response.text
    with client.app.state.test_session_factory() as session:
        stored = session.get(AuthSession, token_hash(raw_cookie))
        assert stored is not None and raw_cookie not in stored.token_hash


def test_email_verification_is_not_required_for_login_or_session_use(client: TestClient) -> None:
    _create_user(client, "pending@example.test", verified=False)
    _, headers = _csrf(client)
    response = client.post("/api/v1/auth/login", json={
        "email": "pending@example.test", "password": "correct-horse-battery-staple",
    }, headers=headers)
    assert response.status_code == 200
    assert response.json()["user"]["email_verified"] is False
    assert client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("persist_expired_challenge", [False, True])
def test_invalid_or_expired_verification_token_is_rejected_without_changing_user(
    client: TestClient, persist_expired_challenge: bool,
) -> None:
    user = _create_user(client, "verification-token@example.test", verified=False)
    original_hash = user.password_hash
    raw_token = "verification-token-for-test-only-000000000000"
    if persist_expired_challenge:
        with client.app.state.test_session_factory.begin() as session:
            session.add(AuthChallenge(
                token_hash=token_hash(raw_token), kind="email_verification", user_id=user.id,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            ))
    _, headers = _csrf(client)

    response = client.post("/api/v1/auth/verify-email", json={
        "token": raw_token, "password": "long-unique-password", "confirm_password": "long-unique-password",
    }, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "This verification link is invalid or expired."
    with client.app.state.test_session_factory() as session:
        unchanged = session.get(User, user.id)
        assert unchanged.email_verified is False and unchanged.password_hash == original_hash


@pytest.mark.parametrize("persist_expired_challenge", [False, True])
def test_invalid_or_expired_recovery_token_does_not_change_password(
    client: TestClient, persist_expired_challenge: bool,
) -> None:
    user = _create_user(client, "recovery-token@example.test")
    original_hash = user.password_hash
    raw_token = "recovery-token-for-test-only-000000000000000"
    if persist_expired_challenge:
        with client.app.state.test_session_factory.begin() as session:
            session.add(AuthChallenge(
                token_hash=token_hash(raw_token), kind="password_reset", user_id=user.id,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            ))
    _, headers = _csrf(client)

    response = client.post("/api/v1/auth/reset-password", json={
        "token": raw_token, "password": "new-unique-password-long", "confirm_password": "new-unique-password-long",
    }, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "This password reset link is invalid or expired."
    with client.app.state.test_session_factory() as session:
        assert session.get(User, user.id).password_hash == original_hash


def test_successful_password_reset_invalidates_existing_sessions(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.api.v1.auth.check_rate_limit", lambda *_args, **_kwargs: None)
    user = _create_user(client, "session-reset@example.test")
    _, login_headers = _csrf(client)
    login = client.post("/api/v1/auth/login", json={
        "email": user.email, "password": "correct-horse-battery-staple",
    }, headers=login_headers)
    assert login.status_code == 200
    authenticated_cookie = client.cookies.get("inscope_session")
    assert authenticated_cookie

    anonymous_cookie = "anonymous-reset-session-cookie-for-test"
    reset_csrf = "csrf-reset-token-for-test-only"
    raw_token = "password-reset-token-for-test-only-000000000000"
    with client.app.state.test_session_factory.begin() as session:
        session.add(AuthSession(
            token_hash=token_hash(anonymous_cookie), csrf_hash=token_hash(reset_csrf),
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        ))
        session.add(AuthChallenge(
            token_hash=token_hash(raw_token), kind="password_reset", user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        ))

    client.cookies.clear()
    client.cookies.set("inscope_session", anonymous_cookie)
    response = client.post("/api/v1/auth/reset-password", json={
        "token": raw_token, "password": "new-unique-password-long", "confirm_password": "new-unique-password-long",
    }, headers={"Origin": ORIGIN, "X-CSRF-Token": reset_csrf})

    assert response.status_code == 200
    client.cookies.clear()
    client.cookies.set("inscope_session", authenticated_cookie)
    assert client.get("/api/v1/auth/me").status_code == 401
    with client.app.state.test_session_factory() as session:
        updated = session.get(User, user.id)
        assert verify_password("new-unique-password-long", updated.password_hash)
        assert session.get(AuthChallenge, token_hash(raw_token)).consumed_at is not None


@pytest.mark.parametrize(
    ("email", "password", "create_account"),
    [
        ("known@example.test", "incorrect-password", True),
        ("unknown@example.test", "incorrect-password", False),
    ],
)
def test_invalid_credentials_share_generic_error_and_do_not_authenticate(
    client: TestClient, email: str, password: str, create_account: bool,
) -> None:
    if create_account:
        _create_user(client, email)
    _, headers = _csrf(client)

    response = client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Email or password is incorrect."
    assert email not in response.text and password not in response.text
    with client.app.state.test_session_factory() as session:
        assert session.scalar(select(AuthSession).where(AuthSession.user_id.is_not(None))) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": "some-secret-value"},
        {"email": "valid@example.test", "password": ""},
        {"email": "valid@example.test"},
    ],
)
def test_malformed_login_is_rejected_without_echoing_input_or_authenticating(
    client: TestClient, payload: dict[str, str],
) -> None:
    _, headers = _csrf(client)

    response = client.post("/api/v1/auth/login", json=payload, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "The request did not match the expected format."
    assert "some-secret-value" not in response.text
    with client.app.state.test_session_factory() as session:
        assert session.scalar(select(AuthSession).where(AuthSession.user_id.is_not(None))) is None


def test_verified_account_without_password_hash_cannot_sign_in(client: TestClient) -> None:
    user = _create_user(client, "passwordless@example.test")
    with client.app.state.test_session_factory.begin() as session:
        session.get(User, user.id).password_hash = None
    _, headers = _csrf(client)

    response = client.post("/api/v1/auth/login", json={
        "email": user.email, "password": "incorrect-password",
    }, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Email or password is incorrect."
    with client.app.state.test_session_factory() as session:
        assert session.scalar(select(AuthSession).where(AuthSession.user_id.is_not(None))) is None


def test_password_recovery_without_email_delivery_is_explicitly_unavailable(client: TestClient) -> None:
    user = _create_user(client, "no-recovery-mail@example.test", verified=False)
    _, headers = _csrf(client)
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": user.email},
        headers=headers,
    )
    assert response.status_code == 503
    assert "Password recovery is unavailable" in response.json()["error"]["message"]
    with client.app.state.test_session_factory() as session:
        assert session.scalar(
            select(AuthChallenge).where(
                AuthChallenge.user_id == user.id,
                AuthChallenge.kind == "password_reset",
            )
        ) is None


def test_email_recovery_is_generic_and_reset_token_is_one_time(client: TestClient, monkeypatch) -> None:
    client.app.state.settings.smtp_host = "smtp.example.test"
    client.app.state.settings.smtp_from_email = "no-reply@example.test"
    deliveries: list[str] = []
    monkeypatch.setattr("app.api.v1.auth.send_email", lambda _settings, _to, _subject, body: deliveries.append(body))
    user = _create_user(client, "recover@example.test", verified=False)
    _, headers = _csrf(client)
    response = client.post("/api/v1/auth/forgot-password", json={"email": user.email}, headers=headers)
    assert response.status_code == 202
    assert response.json() == {"message": "If the address belongs to an account and email delivery is available, instructions will be sent."}
    raw_token = re.search(r"[?&]token=([A-Za-z0-9_-]+)", deliveries[0]).group(1)
    csrf, reset_headers = _csrf(client)
    reset = client.post("/api/v1/auth/reset-password", json={
        "token": raw_token, "password": "another-long-password", "confirm_password": "another-long-password",
    }, headers=reset_headers)
    assert reset.status_code == 200
    with client.app.state.test_session_factory() as session:
        assert session.get(AuthChallenge, token_hash(raw_token)).consumed_at is not None
    csrf, login_headers = _csrf(client)
    login = client.post("/api/v1/auth/login", json={"email": user.email, "password": "another-long-password"}, headers=login_headers)
    assert login.status_code == 200
    logged_out = client.post("/api/v1/auth/logout", headers={
        "Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"],
    })
    assert logged_out.status_code == 204
    _, replay_headers = _csrf(client)
    replay = client.post("/api/v1/auth/reset-password", json={
        "token": raw_token, "password": "third-long-password", "confirm_password": "third-long-password",
    }, headers=replay_headers)
    assert replay.status_code == 400
    with client.app.state.test_session_factory() as session:
        assert verify_password("another-long-password", session.get(User, user.id).password_hash)


def test_workspace_api_requires_login_and_isolates_two_accounts(client: TestClient) -> None:
    client.app.dependency_overrides.pop(get_db_session, None)
    first = _create_user(client, "first@example.test")
    second = _create_user(client, "second@example.test")
    assert client.get("/api/v1/explorations").status_code == 401

    _, headers = _csrf(client)
    login = client.post("/api/v1/auth/login", json={"email": first.email, "password": "correct-horse-battery-staple"}, headers=headers)
    assert login.status_code == 200
    with client.app.state.test_session_factory.begin() as session:
        exploration = Exploration(start_url="https://example.com/", max_pages=1, max_depth=0, owner_id=first.id)
        session.add(exploration)
        session.flush()
        exploration_id = str(exploration.id)
    owned_rows = client.get("/api/v1/explorations")
    assert owned_rows.status_code == 200 and len(owned_rows.json()) == 1

    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]})
    _, second_headers = _csrf(client)
    second_login = client.post("/api/v1/auth/login", json={"email": second.email, "password": "correct-horse-battery-staple"}, headers=second_headers)
    assert second_login.status_code == 200
    assert client.get("/api/v1/explorations").json() == []
    assert client.get(f"/api/v1/explorations/{exploration_id}").status_code == 404


def test_owned_records_are_filtered_from_reads_and_bulk_updates(client: TestClient) -> None:
    factory = client.app.state.test_session_factory
    first_owner, second_owner = uuid4(), uuid4()
    exploration_id = uuid4()
    with factory() as session:
        session.info["owner_id"] = first_owner
        exploration = Exploration(id=exploration_id, start_url="https://one.example/", max_pages=1, max_depth=0)
        session.add(exploration)
        page = ExplorationPage(exploration_id=exploration_id, url="https://one.example/", depth=0)
        session.add(page)
        session.commit()
        assert exploration.owner_id == first_owner
        page_id = page.id
    with factory() as session:
        session.info["owner_id"] = second_owner
        assert session.get(Exploration, exploration_id) is None
        assert session.get(ExplorationPage, page_id) is None
        result = session.execute(update(Exploration).where(Exploration.id == exploration_id).values(status="failed"))
        assert result.rowcount == 0
        session.commit()
    with factory() as session:
        session.info["owner_id"] = first_owner
        row = session.get(Exploration, exploration_id)
        assert row is not None and row.status == "queued"
        assert session.get(ExplorationPage, page_id) is not None


def test_project_links_and_metadata_follow_the_same_workspace_owner(client: TestClient) -> None:
    factory = client.app.state.test_session_factory
    first_owner, second_owner = uuid4(), uuid4()
    exploration_id, project_id = uuid4(), uuid4()
    with factory() as session:
        session.info["owner_id"] = first_owner
        session.add(Exploration(
            id=exploration_id,
            start_url="https://project.example/",
            max_pages=1,
            max_depth=0,
        ))
        session.add(Project(id=project_id, name="Owned project", target_url="https://project.example/"))
        session.flush()
        session.add(ProjectMetadata(project_id=project_id, description="Private project metadata"))
        session.add(ProjectExploration(exploration_id=exploration_id, project_id=project_id))
        session.commit()

    with factory() as session:
        session.info["owner_id"] = second_owner
        assert session.get(Project, project_id) is None
        assert session.get(Exploration, exploration_id) is None
        assert session.get(ProjectMetadata, project_id) is None
        assert session.get(ProjectExploration, exploration_id) is None

    with factory() as session:
        session.info["owner_id"] = first_owner
        assert session.get(Project, project_id) is not None
        assert session.get(Exploration, exploration_id) is not None
        assert session.get(ProjectMetadata, project_id).description == "Private project metadata"
        assert session.get(ProjectExploration, exploration_id).project_id == project_id


def test_workspace_owner_cannot_link_records_from_another_account(client: TestClient) -> None:
    factory = client.app.state.test_session_factory
    first_owner, second_owner = uuid4(), uuid4()
    exploration_id, project_id = uuid4(), uuid4()
    with factory() as session:
        session.info["owner_id"] = first_owner
        session.add(Exploration(
            id=exploration_id,
            start_url="https://private.example/",
            max_pages=1,
            max_depth=0,
        ))
        session.add(Project(id=project_id, name="Private project", target_url="https://private.example/"))
        session.commit()

    with factory() as session:
        session.info["owner_id"] = second_owner
        session.add(ProjectExploration(exploration_id=exploration_id, project_id=project_id))
        with pytest.raises(PermissionError):
            session.flush()
