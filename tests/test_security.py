import bcrypt

from app.security import (
    AuthenticatedPrincipal,
    _normalize_user_roles,
    _parse_roles,
    _safe_identifier,
    _verify_password,
)


def test_parse_roles_supports_multiple_formats():
    assert _parse_roles(None) == []
    assert _parse_roles(["ROLE_USER", "ROLE_ADMIN"]) == ["ROLE_USER", "ROLE_ADMIN"]
    assert _parse_roles(("ROLE_USER", "ROLE_ADMIN")) == ["ROLE_USER", "ROLE_ADMIN"]
    assert _parse_roles('["ROLE_USER", "ROLE_ADMIN"]') == ["ROLE_USER", "ROLE_ADMIN"]
    assert _parse_roles("ROLE_USER, ROLE_ADMIN") == ["ROLE_USER", "ROLE_ADMIN"]
    assert _parse_roles("ROLE_USER") == ["ROLE_USER"]


def test_normalize_user_roles_enforces_role_user_once():
    assert _normalize_user_roles([]) == ["ROLE_USER"]
    assert _normalize_user_roles(["ROLE_USER", "ROLE_USER"]) == ["ROLE_USER"]
    assert _normalize_user_roles(["ROLE_ADMIN"]) == ["ROLE_ADMIN", "ROLE_USER"]


def test_verify_password_plaintext_and_bcrypt():
    assert _verify_password("secret", "secret") is True
    assert _verify_password("secret", "other") is False

    hashed = bcrypt.hashpw("secret".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    assert _verify_password("secret", hashed) is True
    assert _verify_password("other", hashed) is False


def test_safe_identifier_validation():
    assert _safe_identifier("valid_name_1") == "valid_name_1"

    try:
        _safe_identifier("invalid-name")
        assert False, "Expected ValueError for invalid SQL identifier"
    except ValueError:
        pass


def test_authenticated_principal_access_level_mapping():
    team = AuthenticatedPrincipal(principal_type="team", principal_id="t1", username="team", roles=[])
    user = AuthenticatedPrincipal(principal_type="user", principal_id="u1", username="user", roles=["ROLE_USER"])
    super_admin = AuthenticatedPrincipal(
        principal_type="user",
        principal_id="u2",
        username="super",
        roles=["ROLE_SUPER_ADMIN"],
    )

    assert team.access_level == "team"
    assert user.access_level == "user"
    assert super_admin.access_level == "super_admin"
    assert super_admin.is_super_admin is True
