"""Tests for user self-service profile endpoints (GET/PUT /auth/me, PUT /auth/me/password).

Covers:
- GET /auth/me — returns email, username, principal_id
- PUT /auth/me — updates email and/or display name
- PUT /auth/me/password — changes password with current password verification
- Forbidden for team principals
- Error handling for not-found, invalid current password, failed updates
"""

from unittest.mock import MagicMock, patch, PropertyMock
from argon2 import PasswordHasher

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.auth import AuthModule, UserProfileResponse, UpdateProfileRequest, ChangePasswordRequest


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def password_hasher():
    return PasswordHasher()


@pytest.fixture
def hashed_password(password_hasher):
    return password_hasher.hash("currentPass123")


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.auth_users_table = "user"
    settings.auth_user_id_column = "id"
    settings.auth_username_column = "email"
    settings.auth_user_display_name_column = "username"
    settings.auth_password_column = "password"
    settings.ws_to_backend_api_key = ""
    settings.enable_monetisation = False
    return settings


@pytest.fixture
def mock_user_record(hashed_password):
    return {
        "id": "user-123",
        "email": "scout@test.nl",
        "username": "ScoutLeader",
        "password": hashed_password,
    }


@pytest.fixture
def user_principal():
    principal = MagicMock()
    principal.principal_type = "user"
    principal.principal_id = "user-123"
    principal.username = "ScoutLeader"
    principal.roles = ["user"]
    return principal


@pytest.fixture
def team_principal():
    principal = MagicMock()
    principal.principal_type = "team"
    principal.principal_id = "team-456"
    principal.username = "TeamAlpha"
    principal.roles = []
    return principal


@pytest.fixture
def mock_table():
    """Mock a SQLAlchemy Table with columns property."""
    table = MagicMock()
    # Simulate a table without updated_at column by default
    table.c = MagicMock()
    table.c.__contains__ = MagicMock(return_value=False)
    return table


def _build_test_client(mock_settings, principal, mock_user_record, mock_table):
    """Build a FastAPI test client with mocked dependencies."""
    from app.dependencies import require_authenticated_principal
    from app.database import get_db_session

    app = FastAPI()
    ws_publisher = MagicMock()

    with patch("app.modules.auth.get_settings", return_value=mock_settings):
        auth_module = AuthModule(ws_publisher)

    # Mock the repository
    auth_module._user_repo = MagicMock()
    auth_module._user_repo.get_user_by_id = MagicMock(return_value=mock_user_record)
    auth_module._user_repo.update_user_without_commit = MagicMock()
    auth_module._user_repo.commit_changes = MagicMock()
    auth_module._user_repo.rollback_on_error = MagicMock()
    auth_module._user_repo.get_user_table = MagicMock(return_value=mock_table)

    with patch("app.modules.auth.get_settings", return_value=mock_settings):
        router = auth_module.build_router()

    app.include_router(router, prefix="/api")

    # Override dependencies — must override the actual callable, not the Annotated alias
    mock_db = MagicMock()

    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: mock_db

    client = TestClient(app)
    return client, auth_module, mock_db


# ── GET /auth/me tests ──────────────────────────────────────────────────────

class TestGetMyProfile:
    def test_returns_profile_for_user_principal(self, mock_settings, user_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "scout@test.nl"
        assert data["username"] == "ScoutLeader"
        assert data["principal_id"] == "user-123"

    def test_returns_403_for_team_principal(self, mock_settings, team_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, _, _ = _build_test_client(mock_settings, team_principal, mock_user_record, mock_table)

        resp = client.get("/api/auth/me")
        assert resp.status_code == 403

    def test_returns_404_when_user_not_found(self, mock_settings, user_principal, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, None, mock_table)
        module._user_repo.get_user_by_id.return_value = None

        resp = client.get("/api/auth/me")
        assert resp.status_code == 404


# ── PUT /auth/me tests ──────────────────────────────────────────────────────

class TestUpdateMyProfile:
    def test_updates_email_and_username(self, mock_settings, user_principal, mock_user_record, mock_table):
        updated_record = {**mock_user_record, "email": "new@test.nl", "username": "NewName"}

        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        # After update, get_user_by_id returns the updated record
        module._user_repo.get_user_by_id.return_value = updated_record

        resp = client.put("/api/auth/me", json={"email": "new@test.nl", "username": "NewName"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "new@test.nl"
        assert data["username"] == "NewName"
        module._user_repo.update_user_without_commit.assert_called_once()
        module._user_repo.commit_changes.assert_called_once()

    def test_updates_only_email(self, mock_settings, user_principal, mock_user_record, mock_table):
        updated_record = {**mock_user_record, "email": "changed@test.nl"}

        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        module._user_repo.get_user_by_id.return_value = updated_record

        resp = client.put("/api/auth/me", json={"email": "changed@test.nl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "changed@test.nl"

    def test_updates_only_username(self, mock_settings, user_principal, mock_user_record, mock_table):
        updated_record = {**mock_user_record, "username": "UpdatedName"}

        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        module._user_repo.get_user_by_id.return_value = updated_record

        resp = client.put("/api/auth/me", json={"username": "UpdatedName"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "UpdatedName"

    def test_returns_403_for_team(self, mock_settings, team_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, _, _ = _build_test_client(mock_settings, team_principal, mock_user_record, mock_table)

        resp = client.put("/api/auth/me", json={"email": "new@test.nl"})
        assert resp.status_code == 403

    def test_no_changes_returns_current_profile(self, mock_settings, user_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        resp = client.put("/api/auth/me", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "scout@test.nl"
        module._user_repo.update_user_without_commit.assert_not_called()

    def test_handles_db_error_on_update(self, mock_settings, user_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        module._user_repo.update_user_without_commit.side_effect = Exception("DB error")

        resp = client.put("/api/auth/me", json={"email": "fail@test.nl"})
        assert resp.status_code == 400
        module._user_repo.rollback_on_error.assert_called_once()


# ── PUT /auth/me/password tests ─────────────────────────────────────────────

class TestChangeMyPassword:
    def test_changes_password_successfully(self, mock_settings, user_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        resp = client.put("/api/auth/me/password", json={
            "current_password": "currentPass123",
            "new_password": "newStrongPass456",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "message_key" in data
        module._user_repo.update_user_without_commit.assert_called_once()
        module._user_repo.commit_changes.assert_called_once()

    def test_rejects_wrong_current_password(self, mock_settings, user_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, _, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        resp = client.put("/api/auth/me/password", json={
            "current_password": "wrongPassword",
            "new_password": "newStrongPass456",
        })
        assert resp.status_code == 400
        assert "currentIncorrect" in resp.json().get("detail", "")

    def test_returns_403_for_team(self, mock_settings, team_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, _, _ = _build_test_client(mock_settings, team_principal, mock_user_record, mock_table)

        resp = client.put("/api/auth/me/password", json={
            "current_password": "currentPass123",
            "new_password": "newStrongPass456",
        })
        assert resp.status_code == 403

    def test_returns_404_for_missing_user(self, mock_settings, user_principal, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, None, mock_table)
        module._user_repo.get_user_by_id.return_value = None

        resp = client.put("/api/auth/me/password", json={
            "current_password": "currentPass123",
            "new_password": "newStrongPass456",
        })
        assert resp.status_code == 404

    def test_rejects_short_new_password(self, mock_settings, user_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, _, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        resp = client.put("/api/auth/me/password", json={
            "current_password": "currentPass123",
            "new_password": "short",
        })
        assert resp.status_code == 422  # pydantic validation

    def test_handles_db_error_on_password_change(self, mock_settings, user_principal, mock_user_record, mock_table):
        with patch("app.modules.auth.get_settings", return_value=mock_settings):
            client, module, _ = _build_test_client(mock_settings, user_principal, mock_user_record, mock_table)

        module._user_repo.update_user_without_commit.side_effect = Exception("DB write error")

        resp = client.put("/api/auth/me/password", json={
            "current_password": "currentPass123",
            "new_password": "newStrongPass456",
        })
        assert resp.status_code == 400
        module._user_repo.rollback_on_error.assert_called_once()
