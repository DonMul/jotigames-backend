import hashlib
import json
import re
import secrets
from uuid import uuid4
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
import bcrypt
from sqlalchemy import MetaData, Table, and_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import engine
from app.models import ApiAuthToken

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_argon2_hasher = PasswordHasher()


class RegistrationError(Exception):
    """Domain exception that carries i18n-friendly registration/auth message keys."""

    def __init__(self, message_key: str):
        """Initialize exception with a localization-friendly message key."""
        super().__init__(message_key)
        self.message_key = message_key


@dataclass(slots=True)
class AuthenticatedPrincipal:
    """Authenticated identity context used by API dependencies and route handlers.

    Attributes:
        principal_type: Either `user` or `team`.
        principal_id: Stable identifier for the principal in persistent storage.
        username: Display/login name of the principal.
        roles: Role list (for users) used to derive authorization level.
    """

    principal_type: Literal["user", "team"]
    principal_id: str
    username: str
    roles: list[str]

    @property
    def is_super_admin(self) -> bool:
        """Return whether this principal has global super-admin privileges."""
        return "ROLE_SUPER_ADMIN" in self.roles

    @property
    def is_admin(self) -> bool:
        """Return whether this principal has admin privileges (admin or super-admin)."""
        return "ROLE_ADMIN" in self.roles or "ROLE_SUPER_ADMIN" in self.roles

    @property
    def access_level(self) -> str:
        """Map principal identity to a coarse-grained access level label."""
        if self.principal_type == "team":
            return "team"
        if self.is_super_admin:
            return "super_admin"
        if self.is_admin:
            return "admin"
        return "user"


def _safe_identifier(name: str) -> str:
    """Validate a SQL identifier sourced from configuration before usage.

    This protects dynamic table/column lookups from malformed or dangerous
    values and prevents accidental SQL injection via identifier config.
    """
    if not _IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"Invalid SQL identifier configured: {name}")
    return name


def _hash_token(raw_token: str) -> str:
    """Hash raw bearer tokens before persistence so plaintext tokens are not stored."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _hash_user_password(plain_password: str) -> str:
    """Hash a user password using Argon2 for secure at-rest credential storage."""
    return _argon2_hasher.hash(plain_password)


def _verify_password(plain_password: str, stored_password: str) -> bool:
    """Verify a plaintext password against Argon2, bcrypt, or legacy plaintext.

    Supported formats are checked by prefix:
    - Argon2 (`$argon2...`)
    - bcrypt (`$2a$`, `$2b$`, `$2y$`)
    - constant-time plaintext fallback for legacy records
    """
    if not stored_password:
        return False

    if stored_password.startswith("$argon2"):
        try:
            _argon2_hasher.verify(stored_password, plain_password)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False

    if stored_password.startswith("$2a$") or stored_password.startswith("$2b$") or stored_password.startswith("$2y$"):
        try:
            return bcrypt.checkpw(plain_password.encode("utf-8"), stored_password.encode("utf-8"))
        except ValueError:
            return False

    return secrets.compare_digest(plain_password, stored_password)


def _parse_roles(value: object) -> list[str]:
    """Parse role values from list/tuple/JSON/comma-separated database formats."""
    if value is None:
        return []

    if isinstance(value, list):
        return [str(role) for role in value if str(role)]

    if isinstance(value, tuple):
        return [str(role) for role in value if str(role)]

    text_value = str(value).strip()
    if not text_value:
        return []

    if text_value.startswith("[") and text_value.endswith("]"):
        try:
            parsed = json.loads(text_value)
            if isinstance(parsed, list):
                return [str(role) for role in parsed if str(role)]
        except json.JSONDecodeError:
            pass

    if "," in text_value:
        return [part.strip() for part in text_value.split(",") if part.strip()]

    return [text_value]


def _normalize_user_roles(roles: list[str]) -> list[str]:
    """Normalize roles by enforcing ROLE_USER baseline and removing duplicates."""
    normalized = [role for role in roles if role]
    if "ROLE_USER" not in normalized:
        normalized.append("ROLE_USER")
    return list(dict.fromkeys(normalized))


def _first_available_column(table: Table, preferred: str, fallbacks: list[str]) -> Optional[str]:
    """Find the first existing column among preferred and fallback candidates."""
    for candidate in [preferred, *fallbacks]:
        if candidate in table.c:
            return candidate
    return None


def _fetch_principal_row(
    db: Session,
    *,
    table_name: str,
    id_column_name: str,
    username_column_name: str,
    password_column_name: str,
    roles_column_name: Optional[str],
    username: str,
) -> Optional[dict]:
    """Load principal auth fields from a dynamic table definition.

    The function centralizes database selection logic used by both user and
    team authentication paths while keeping table/column names configurable.
    """
    table = _safe_identifier(table_name)
    id_col = _safe_identifier(id_column_name)
    username_col = _safe_identifier(username_column_name)
    password_col = _safe_identifier(password_column_name)
    roles_col = _safe_identifier(roles_column_name) if roles_column_name else None

    metadata = MetaData()
    auth_table = Table(table, metadata, autoload_with=engine)

    selected_columns = [
        auth_table.c[id_col].label("principal_id"),
        auth_table.c[username_col].label("username"),
        auth_table.c[password_col].label("password_hash"),
    ]
    if roles_col:
        selected_columns.append(auth_table.c[roles_col].label("roles_raw"))

    query = select(*selected_columns).where(auth_table.c[username_col] == username).limit(1)
    row = db.execute(query).mappings().first()
    if row is None:
        return None
    return dict(row)


def _authenticate_principal(
    db: Session,
    *,
    principal_type: Literal["user", "team"],
    table_name: str,
    id_column_name: str,
    username_column_name: str,
    password_column_name: str,
    roles_column_name: Optional[str],
    username: str,
    password: str,
) -> Optional[AuthenticatedPrincipal]:
    """Authenticate a principal against a configured credential source.

    Returns an `AuthenticatedPrincipal` when username/password validation
    succeeds, otherwise `None`.
    """
    row = _fetch_principal_row(
        db,
        table_name=table_name,
        id_column_name=id_column_name,
        username_column_name=username_column_name,
        password_column_name=password_column_name,
        roles_column_name=roles_column_name,
        username=username,
    )
    if row is None:
        return None

    if not _verify_password(password, row["password_hash"]):
        return None

    return AuthenticatedPrincipal(
        principal_type=principal_type,
        principal_id=str(row["principal_id"]),
        username=str(row["username"]),
        roles=_parse_roles(row.get("roles_raw")),
    )


def authenticate_user(db: Session, username: str, password: str) -> Optional[AuthenticatedPrincipal]:
    """Authenticate a user account and normalize role claims for authorization."""
    settings = get_settings()
    principal = _authenticate_principal(
        db,
        principal_type="user",
        table_name=settings.auth_users_table,
        id_column_name=settings.auth_user_id_column,
        username_column_name=settings.auth_username_column,
        password_column_name=settings.auth_password_column,
        roles_column_name=settings.auth_user_roles_column,
        username=username,
        password=password,
    )
    if principal is None:
        return None
    principal.roles = _normalize_user_roles(principal.roles)
    return principal


def register_user(db: Session, *, email: str, username: str, password: str) -> tuple[str, str]:
    """Create a new user account and issue an email-verification token.

    The function validates uniqueness for both email and username, writes the
    hashed password, initializes default roles, and returns identifiers needed
    by mail workflows.

    Raises:
        RegistrationError: For duplicate identities or missing schema columns.
    """
    settings = get_settings()
    table_name = _safe_identifier(settings.auth_users_table)
    metadata = MetaData()
    user_table = Table(table_name, metadata, autoload_with=engine)

    id_col = _safe_identifier(settings.auth_user_id_column)
    email_col = _safe_identifier(settings.auth_username_column)
    username_col = _safe_identifier(settings.auth_user_display_name_column)
    password_col = _safe_identifier(settings.auth_password_column)
    roles_col = _safe_identifier(settings.auth_user_roles_column)

    is_verified_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_is_verified_column),
        ["isVerified"],
    )
    verification_token_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_email_verification_token_column),
        ["emailVerificationToken"],
    )
    verification_requested_at_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_email_verification_requested_at_column),
        ["emailVerificationRequestedAt"],
    )
    if not verification_token_col:
        raise RegistrationError("auth.verify.tokenColumnMissing")

    email_exists = (
        db.execute(select(user_table.c[id_col]).where(user_table.c[email_col] == email).limit(1)).mappings().first()
        is not None
    )
    if email_exists:
        raise RegistrationError("auth.register.emailAlreadyUsed")

    username_exists = (
        db.execute(select(user_table.c[id_col]).where(user_table.c[username_col] == username).limit(1)).mappings().first()
        is not None
    )
    if username_exists:
        raise RegistrationError("auth.register.usernameAlreadyUsed")

    user_id = str(uuid4())
    verification_token = secrets.token_hex(32)
    now = datetime.now(UTC).replace(tzinfo=None)

    values = {
        id_col: user_id,
        email_col: email,
        username_col: username,
        password_col: _hash_user_password(password),
        roles_col: json.dumps(["ROLE_USER"]),
    }
    if is_verified_col:
        values[is_verified_col] = False
    if verification_token_col:
        values[verification_token_col] = verification_token
    if verification_requested_at_col:
        values[verification_requested_at_col] = now

    db.execute(user_table.insert().values(**values))
    db.commit()

    return user_id, verification_token


def verify_user_email_token(db: Session, *, token: str) -> bool:
    """Consume an email verification token and mark a user as verified.

    Handles optional pending-email promotion and clears one-time verification
    fields after successful confirmation.
    """
    settings = get_settings()
    table_name = _safe_identifier(settings.auth_users_table)
    metadata = MetaData()
    user_table = Table(table_name, metadata, autoload_with=engine)

    id_col = _safe_identifier(settings.auth_user_id_column)
    email_col = _safe_identifier(settings.auth_username_column)

    is_verified_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_is_verified_column),
        ["isVerified"],
    )
    verification_token_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_email_verification_token_column),
        ["emailVerificationToken"],
    )
    verification_requested_at_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_email_verification_requested_at_column),
        ["emailVerificationRequestedAt"],
    )
    pending_email_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_pending_email_column),
        ["pendingEmail"],
    )

    if not verification_token_col:
        raise RegistrationError("auth.verify.tokenColumnMissing")

    row = (
        db.execute(
            select(user_table).where(user_table.c[verification_token_col] == token).limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        return False

    update_values: dict[str, object] = {}
    if pending_email_col and row.get(pending_email_col):
        pending_email = str(row.get(pending_email_col))
        existing_user = (
            db.execute(
                select(user_table.c[id_col])
                .where(and_(user_table.c[email_col] == pending_email, user_table.c[id_col] != row[id_col]))
                .limit(1)
            )
            .mappings()
            .first()
        )
        if existing_user is not None:
            raise RegistrationError("auth.verify.pendingEmailConflict")
        update_values[email_col] = pending_email
        update_values[pending_email_col] = None

    if is_verified_col:
        update_values[is_verified_col] = True
    if verification_token_col:
        update_values[verification_token_col] = None
    if verification_requested_at_col:
        update_values[verification_requested_at_col] = None

    db.execute(user_table.update().where(user_table.c[id_col] == row[id_col]).values(**update_values))
    db.commit()
    return True


def create_password_reset_token_if_verified(db: Session, *, email: str) -> Optional[tuple[str, str]]:
    """Create password reset token for verified users only.

    Returns `(username, reset_token)` when issuance is allowed, otherwise
    `None` for unknown/unverified accounts to avoid account enumeration.
    """
    settings = get_settings()
    table_name = _safe_identifier(settings.auth_users_table)
    metadata = MetaData()
    user_table = Table(table_name, metadata, autoload_with=engine)

    id_col = _safe_identifier(settings.auth_user_id_column)
    email_col = _safe_identifier(settings.auth_username_column)
    username_col = _safe_identifier(settings.auth_user_display_name_column)

    is_verified_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_is_verified_column),
        ["isVerified"],
    )
    reset_token_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_password_reset_token_column),
        ["passwordResetToken"],
    )
    reset_requested_at_col = _first_available_column(
        user_table,
        _safe_identifier(settings.auth_user_password_reset_requested_at_column),
        ["passwordResetRequestedAt"],
    )

    if not reset_token_col or not reset_requested_at_col:
        raise RegistrationError("auth.reset.tokenColumnsMissing")

    row = (
        db.execute(select(user_table).where(user_table.c[email_col] == email).limit(1))
        .mappings()
        .first()
    )
    if row is None:
        return None

    if is_verified_col and not bool(row.get(is_verified_col)):
        return None

    reset_token = secrets.token_hex(32)
    requested_at = datetime.now(UTC).replace(tzinfo=None)

    db.execute(
        user_table.update()
        .where(user_table.c[id_col] == row[id_col])
        .values(
            **{
                reset_token_col: reset_token,
                reset_requested_at_col: requested_at,
            }
        )
    )
    db.commit()

    return str(row.get(username_col, "")), reset_token


def authenticate_team(db: Session, game_code: str, team_code: str) -> Optional[AuthenticatedPrincipal]:
    """Authenticate a team principal using `(game_code, team_code)` pair."""
    settings = get_settings()

    team_table_name = _safe_identifier(settings.auth_teams_table)
    team_id_col = _safe_identifier(settings.auth_team_id_column)
    team_code_col = _safe_identifier(settings.auth_team_code_column)
    team_game_id_col = _safe_identifier(settings.auth_team_game_id_column)
    team_name_col = _safe_identifier(settings.auth_team_name_column)

    game_table_name = _safe_identifier(settings.auth_games_table)
    game_id_col = _safe_identifier(settings.auth_game_id_column)
    game_code_col = _safe_identifier(settings.auth_game_code_column)

    metadata = MetaData()
    team_table = Table(team_table_name, metadata, autoload_with=engine)
    game_table = Table(game_table_name, metadata, autoload_with=engine)

    query = (
        select(
            team_table.c[team_id_col].label("principal_id"),
            team_table.c[team_name_col].label("team_name"),
        )
        .select_from(team_table.join(game_table, team_table.c[team_game_id_col] == game_table.c[game_id_col]))
        .where(
            and_(
                game_table.c[game_code_col] == game_code,
                team_table.c[team_code_col] == team_code,
            )
        )
        .limit(1)
    )

    row = db.execute(query).mappings().first()
    if row is None:
        return None

    return AuthenticatedPrincipal(
        principal_type="team",
        principal_id=str(row["principal_id"]),
        username=str(row["team_name"]),
        roles=[],
    )


def _get_user_principal_details_by_id(db: Session, principal_id: str) -> Optional[dict]:
    """Fetch user principal details used when resolving stored auth tokens."""
    settings = get_settings()
    table = _safe_identifier(settings.auth_users_table)
    id_col = _safe_identifier(settings.auth_user_id_column)
    username_col = _safe_identifier(settings.auth_username_column)
    roles_col = _safe_identifier(settings.auth_user_roles_column)

    metadata = MetaData()
    auth_table = Table(table, metadata, autoload_with=engine)
    query = (
        select(
            auth_table.c[id_col].label("principal_id"),
            auth_table.c[username_col].label("username"),
            auth_table.c[roles_col].label("roles_raw"),
        )
        .where(auth_table.c[id_col] == principal_id)
        .limit(1)
    )
    row = db.execute(query).mappings().first()
    if row is None:
        return None
    return dict(row)


def _get_team_principal_details_by_id(db: Session, principal_id: str) -> Optional[dict]:
    """Fetch team principal details used when resolving stored auth tokens."""
    settings = get_settings()
    table = _safe_identifier(settings.auth_teams_table)
    id_col = _safe_identifier(settings.auth_team_id_column)
    username_col = _safe_identifier(settings.auth_team_name_column)

    metadata = MetaData()
    team_table = Table(table, metadata, autoload_with=engine)
    query = (
        select(
            team_table.c[id_col].label("principal_id"),
            team_table.c[username_col].label("username"),
        )
        .where(team_table.c[id_col] == principal_id)
        .limit(1)
    )
    row = db.execute(query).mappings().first()
    if row is None:
        return None
    return dict(row)


def create_temp_token(db: Session, principal_type: Literal["user", "team"], principal_id: str) -> tuple[str, datetime]:
    """Issue and persist a temporary bearer token for an authenticated principal.

    A secure random token is returned to the caller, while only its hash is
    persisted in `api_auth_tokens` with TTL metadata.
    """
    settings = get_settings()

    now = datetime.now(UTC).replace(tzinfo=None)
    expires_at = now + timedelta(minutes=settings.token_ttl_minutes)
    raw_token = secrets.token_urlsafe(48)
    token_hash = _hash_token(raw_token)

    db.add(
        ApiAuthToken(
            user_id=principal_id,
            principal_type=principal_type,
            principal_id=principal_id,
            token_hash=token_hash,
            issued_at=now,
            expires_at=expires_at,
        )
    )
    db.commit()

    return raw_token, expires_at


def resolve_token_principal(db: Session, raw_token: str) -> Optional[AuthenticatedPrincipal]:
    """Resolve bearer token to principal context if token exists and is unexpired."""
    now = datetime.now(UTC).replace(tzinfo=None)
    token_hash = _hash_token(raw_token)

    token = (
        db.query(ApiAuthToken)
        .filter(ApiAuthToken.token_hash == token_hash, ApiAuthToken.expires_at > now)
        .first()
    )
    if token is None:
        return None

    principal_type = token.principal_type or "user"
    principal_id = token.principal_id or token.user_id

    username = ""
    roles: list[str] = []

    if principal_type == "user":
        details = _get_user_principal_details_by_id(db, principal_id)
        if details:
            username = str(details.get("username", ""))
            roles = _normalize_user_roles(_parse_roles(details.get("roles_raw")))
    else:
        details = _get_team_principal_details_by_id(db, principal_id)
        if details:
            username = str(details.get("username", ""))

    return AuthenticatedPrincipal(
        principal_type=principal_type,
        principal_id=principal_id,
        username=username,
        roles=roles,
    )


def cleanup_expired_tokens(db: Session) -> int:
    """Delete expired auth tokens and return number of removed records."""
    now = datetime.now(UTC).replace(tzinfo=None)
    deleted = db.query(ApiAuthToken).filter(ApiAuthToken.expires_at <= now).delete()
    db.commit()
    return int(deleted)
