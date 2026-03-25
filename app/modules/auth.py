from datetime import datetime
import secrets
from urllib.parse import urlencode
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.dependencies import CurrentLocale, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_SUPER_ADMIN_LABEL
from app.repositories.game_repository import GameRepository
from app.repositories.team_repository import TeamRepository
from app.security import (
    RegistrationError,
    authenticate_team,
    authenticate_user,
    create_password_reset_token_if_verified,
    create_temp_token,
    resolve_token_principal,
    register_user,
    verify_user_email_token,
)
from app.config import get_settings
from app.services.i18n import translate_value
from app.services.mailer import MailerConfigurationError, send_password_reset_email, send_verification_email
from app.services.ws_client import WsEventPublisher


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TeamLoginRequest(BaseModel):
    game_code: str = Field(min_length=1)
    team_code: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=8)
    locale: Optional[str] = Field(default=None)


class PasswordForgotRequest(BaseModel):
    email: EmailStr
    locale: Optional[str] = Field(default=None)


class VerifyRequest(BaseModel):
    token: str = Field(min_length=1)


class VerifyAuthTokenRequest(BaseModel):
    game_id: str = Field(min_length=1, max_length=64)
    auth_token: str = Field(min_length=1, max_length=1024)


class VerifyAuthTokenResponse(BaseModel):
    principal_type: str
    principal_id: str
    access_level: str
    game_id: str
    channel_game: str
    channel_target: str
    has_access: bool


class LoginResponse(BaseModel):
    token_type: str
    access_token: str
    expires_at: datetime
    principal_type: str
    principal_id: str
    username: str
    access_level: str
    roles: list[str]


class MessageKeyResponse(BaseModel):
    message_key: str


class AuthModule(ApiModule):
    name = "auth"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        """Initialize auth module dependencies for access checks and WS integration."""
        self._ws_publisher = ws_publisher
        self._gameRepository = GameRepository()
        self._teamRepository = TeamRepository()

    def build_router(self) -> APIRouter:
        """Build authentication, registration, and token-access verification routes."""
        router = APIRouter(prefix="/auth", tags=["auth"])

        def resolve_mail_locale(locale: Optional[str]) -> str:
            """Normalize user-provided locale to supported mail language codes.

            Supports `en` and `nl`; rejects unsupported values with a
            translation-key HTTP error to keep client localization consistent.
            """
            if locale is None or locale.strip() == "":
                return "en"
            normalized = locale.strip().lower().replace("_", "-").split("-")[0]
            if normalized not in {"en", "nl"}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="auth.locale.unsupported",
                )
            return normalized

        def require_ws_super_admin_key(request: Request) -> None:
            """Validate shared secret used by WS service for privileged auth checks.

            The endpoint accepts key material from dedicated headers or bearer
            auth, then performs constant-time comparison against configured
            backend secret.
            """
            configured_key = get_settings().ws_to_backend_api_key
            if not configured_key:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="auth.ws.superAdminKeyNotConfigured",
                )

            provided_key = request.headers.get("X-WS-Super-Admin-Key") or request.headers.get("X-Admin-Api-Key")
            if not provided_key:
                auth_header = str(request.headers.get("Authorization") or "")
                if auth_header.lower().startswith("bearer "):
                    provided_key = auth_header.split(" ", 1)[1].strip()

            if not provided_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="auth.ws.superAdminKeyRequired",
                )

            if not secrets.compare_digest(str(provided_key), str(configured_key)):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="auth.ws.superAdminKeyInvalid",
                )

        @router.post("/user", response_model=LoginResponse)
        def login(body: LoginRequest, db: DbSession) -> LoginResponse:
            """Authenticate a platform user and issue a temporary bearer token."""
            user = authenticate_user(db, body.email, body.password)
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="auth.user.invalidEmailOrPassword",
                )

            has_valid_user_role = ("ROLE_USER" in user.roles) or ("ROLE_ADMIN" in user.roles) or ("ROLE_SUPER_ADMIN" in user.roles)
            if not has_valid_user_role:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="auth.user.disallowedRole",
                )

            access_token, expires_at = create_temp_token(db, user.principal_type, user.principal_id)

            return LoginResponse(
                token_type="Bearer",
                access_token=access_token,
                expires_at=expires_at,
                principal_type=user.principal_type,
                principal_id=user.principal_id,
                username=user.username,
                access_level=user.access_level,
                roles=user.roles,
            )

        @router.post("/team", response_model=LoginResponse)
        def team_login(body: TeamLoginRequest, db: DbSession) -> LoginResponse:
            """Authenticate a team using game/team code pair and issue token."""
            team = authenticate_team(db, body.game_code, body.team_code)
            if team is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="auth.team.invalidCodes",
                )

            access_token, expires_at = create_temp_token(db, team.principal_type, team.principal_id)

            return LoginResponse(
                token_type="Bearer",
                access_token=access_token,
                expires_at=expires_at,
                principal_type=team.principal_type,
                principal_id=team.principal_id,
                username=team.username,
                access_level=team.access_level,
                roles=team.roles,
            )

        @router.post("/register", response_model=MessageKeyResponse)
        def register(body: RegisterRequest, db: DbSession, locale_header: CurrentLocale) -> MessageKeyResponse:
            """Register a user account and send verification email.

            On success this route always returns an i18n message key response;
            verification is completed asynchronously via email follow-up.
            """
            settings = get_settings()
            locale = resolve_mail_locale(body.locale or locale_header)
            try:
                user_id, verification_token = register_user(
                    db,
                    email=str(body.email),
                    username=body.username,
                    password=body.password,
                )
            except RegistrationError as error:
                status_code = status.HTTP_400_BAD_REQUEST
                if error.message_key in {"auth.verify.tokenColumnMissing"}:
                    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
                raise HTTPException(status_code=status_code, detail=error.message_key) from error

            verify_url = f"{settings.auth_verify_url}?{urlencode({'token': verification_token})}"
            try:
                send_verification_email(
                    to_email=str(body.email),
                    username=body.username,
                    verify_url=verify_url,
                    locale=locale,
                )
            except MailerConfigurationError as error:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="auth.register.emailSendFailed",
                ) from error

            return MessageKeyResponse(
                message_key=translate_value("auth.register.verifyEmailSent", locale=locale_header)
            )

        @router.post("/password/forgot", response_model=MessageKeyResponse)
        def password_forgot(body: PasswordForgotRequest, db: DbSession, locale_header: CurrentLocale) -> MessageKeyResponse:
            """Request password reset without revealing account existence status.

            The endpoint responds with a generic acceptance key regardless of
            whether a reset token was issued, preventing account enumeration.
            """
            settings = get_settings()
            locale = resolve_mail_locale(body.locale or locale_header)

            try:
                reset_data = create_password_reset_token_if_verified(db, email=str(body.email))
            except RegistrationError as error:
                status_code = status.HTTP_400_BAD_REQUEST
                if error.message_key in {"auth.reset.tokenColumnsMissing"}:
                    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
                raise HTTPException(status_code=status_code, detail=error.message_key) from error

            if reset_data is not None:
                username, reset_token = reset_data
                reset_url = f"{settings.auth_password_reset_url}?{urlencode({'token': reset_token})}"
                try:
                    send_password_reset_email(
                        to_email=str(body.email),
                        username=username,
                        reset_url=reset_url,
                        locale=locale,
                    )
                except MailerConfigurationError as error:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)) from error
                except Exception as error:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="auth.reset.emailSendFailed",
                    ) from error

            return MessageKeyResponse(
                message_key=translate_value("auth.reset.requestAccepted", locale=locale_header)
            )

        @router.post("/verify", response_model=MessageKeyResponse)
        def verify(body: VerifyRequest, db: DbSession, locale_header: CurrentLocale) -> MessageKeyResponse:
            """Verify email ownership by consuming one-time verification token."""
            try:
                success = verify_user_email_token(db, token=body.token)
            except RegistrationError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error.message_key) from error

            if not success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="auth.verify.invalidToken",
                )

            return MessageKeyResponse(
                message_key=translate_value("auth.verify.success", locale=locale_header)
            )

        @router.post(
            "/token/verify-access",
            response_model=VerifyAuthTokenResponse,
            summary=f"{ACCESS_SUPER_ADMIN_LABEL} Verify auth token access for game",
        )
        def verify_auth_token_access(
            body: VerifyAuthTokenRequest,
            request: Request,
            db: DbSession,
        ) -> VerifyAuthTokenResponse:
            """Validate token access to a specific game and derive WS channels.

            This route is intended for WS-side authorization handshakes. It
            checks principal validity, confirms game membership/role access, and
            returns canonical channel targets for subscription scoping.
            """
            require_ws_super_admin_key(request)

            game = self._gameRepository.getGameById(db, body.game_id)
            if game is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.notFound")

            principal = resolve_token_principal(db, body.auth_token)
            if principal is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="auth.token.invalidOrExpired",
                )

            has_access = False
            channel_target = f"channel:{body.game_id}"

            if principal.principal_type == "team":
                has_access = self._teamRepository.getTeamByGameIdAndTeamId(db, body.game_id, principal.principal_id) is not None
                channel_target = f"channel:{body.game_id}:{principal.principal_id}"
            else:
                has_access = principal.is_admin or (
                    self._gameRepository.isGameOwnerByGameIdAndUserId(db, body.game_id, principal.principal_id)
                    or self._gameRepository.hasGameManagerByGameIdAndUserId(db, body.game_id, principal.principal_id)
                    or self._gameRepository.hasGameMasterByGameIdAndUserId(db, body.game_id, principal.principal_id)
                )
                channel_target = f"channel:{body.game_id}:admin"

            if not has_access:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="auth.token.gameAccessRequired",
                )

            return VerifyAuthTokenResponse(
                principal_type=principal.principal_type,
                principal_id=principal.principal_id,
                access_level=principal.access_level,
                game_id=body.game_id,
                channel_game=f"channel:{body.game_id}",
                channel_target=channel_target,
                has_access=True,
            )

        return router
