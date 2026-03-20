from functools import lru_cache
from urllib.parse import urlparse, urlunparse
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="JotiGames Backend", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_prefix: str = Field(default="/api", alias="API_PREFIX")

    database_url: str = Field(alias="DATABASE_URL")

    auth_users_table: str = Field(default="user", alias="AUTH_USERS_TABLE")
    auth_user_id_column: str = Field(default="id", alias="AUTH_USER_ID_COLUMN")
    auth_username_column: str = Field(default="email", alias="AUTH_USERNAME_COLUMN")
    auth_user_display_name_column: str = Field(default="username", alias="AUTH_USER_DISPLAY_NAME_COLUMN")
    auth_password_column: str = Field(default="password", alias="AUTH_PASSWORD_COLUMN")
    auth_user_roles_column: str = Field(default="roles", alias="AUTH_USER_ROLES_COLUMN")
    auth_user_is_verified_column: str = Field(default="is_verified", alias="AUTH_USER_IS_VERIFIED_COLUMN")
    auth_user_email_verification_token_column: str = Field(
        default="email_verification_token",
        alias="AUTH_USER_EMAIL_VERIFICATION_TOKEN_COLUMN",
    )
    auth_user_email_verification_requested_at_column: str = Field(
        default="email_verification_requested_at",
        alias="AUTH_USER_EMAIL_VERIFICATION_REQUESTED_AT_COLUMN",
    )
    auth_user_pending_email_column: str = Field(default="pending_email", alias="AUTH_USER_PENDING_EMAIL_COLUMN")
    auth_user_password_reset_token_column: str = Field(
        default="password_reset_token",
        alias="AUTH_USER_PASSWORD_RESET_TOKEN_COLUMN",
    )
    auth_user_password_reset_requested_at_column: str = Field(
        default="password_reset_requested_at",
        alias="AUTH_USER_PASSWORD_RESET_REQUESTED_AT_COLUMN",
    )

    auth_teams_table: str = Field(default="team", alias="AUTH_TEAMS_TABLE")
    auth_team_id_column: str = Field(default="id", alias="AUTH_TEAM_ID_COLUMN")
    auth_team_code_column: str = Field(default="code", alias="AUTH_TEAM_CODE_COLUMN")
    auth_team_game_id_column: str = Field(default="game_id", alias="AUTH_TEAM_GAME_ID_COLUMN")
    auth_team_name_column: str = Field(default="name", alias="AUTH_TEAM_NAME_COLUMN")

    auth_games_table: str = Field(default="game", alias="AUTH_GAMES_TABLE")
    auth_game_id_column: str = Field(default="id", alias="AUTH_GAME_ID_COLUMN")
    auth_game_code_column: str = Field(default="code", alias="AUTH_GAME_CODE_COLUMN")

    token_ttl_minutes: int = Field(default=43200, alias="TOKEN_TTL_MINUTES")

    ws_base_url: Optional[str] = Field(default=None, alias="WS_BASE_URL")
    ws_events_url: Optional[str] = Field(default=None, alias="WS_EVENTS_URL")
    ws_protocol: str = Field(default="http", alias="WS_PROTOCOL")
    ws_host: Optional[str] = Field(default=None, alias="WS_HOST")
    ws_port: Optional[int] = Field(default=None, alias="WS_PORT")
    ws_base_path: str = Field(default="", alias="WS_BASE_PATH")
    ws_event_path: str = Field(default="/admin/events", alias="WS_EVENT_PATH")
    ws_to_backend_api_key: Optional[str] = Field(default=None, alias="WS_TO_BACKEND_API_KEY")
    backend_to_ws_api_key: Optional[str] = Field(default=None, alias="BACKEND_TO_WS_API_KEY")

    ssl_certfile: Optional[str] = Field(default=None, alias="SSL_CERTFILE")
    ssl_keyfile: Optional[str] = Field(default=None, alias="SSL_KEYFILE")
    ssl_keyfile_password: Optional[str] = Field(default=None, alias="SSL_KEYFILE_PASSWORD")

    app_public_base_url: str = Field(default="http://localhost:8000", alias="APP_PUBLIC_BASE_URL")
    auth_verify_path: str = Field(default="/api/auth/verify", alias="AUTH_VERIFY_PATH")
    auth_password_reset_path: str = Field(default="/reset-password", alias="AUTH_PASSWORD_RESET_PATH")

    mailer_dsn: Optional[str] = Field(default=None, alias="MAILER_DSN")
    mailer_from: Optional[str] = Field(default=None, alias="MAILER_FROM")

    default_locale: str = Field(default="en", alias="DEFAULT_LOCALE")
    translations_dir: Optional[str] = Field(default="translations/locales", alias="TRANSLATIONS_DIR")

    @property
    def ws_socket_endpoint(self) -> Optional[str]:
        direct_url = str(self.ws_events_url or "").strip()
        if direct_url:
            try:
                parsed = urlparse(direct_url)
                scheme = parsed.scheme.lower().strip()
                mapped_scheme = scheme
                if scheme == "http":
                    mapped_scheme = "ws"
                elif scheme == "https":
                    mapped_scheme = "wss"
                elif scheme == "":
                    mapped_scheme = "ws"

                path = (parsed.path or "").strip() or "/"
                normalized = parsed._replace(scheme=mapped_scheme, path=path)
                return urlunparse(normalized)
            except Exception:
                return direct_url

        base_url = str(self.ws_base_url or "").strip()
        if not base_url:
            host = str(self.ws_host or "").strip()
            if host:
                if "://" in host:
                    base_url = host
                else:
                    protocol = "wss" if str(self.ws_protocol or "").strip().lower() == "https" else "ws"
                    include_port = bool(self.ws_port) and ":" not in host
                    authority = f"{host}:{self.ws_port}" if include_port else host
                    base_url = f"{protocol}://{authority}"

                base_path = str(self.ws_base_path or "").strip()
                if base_path:
                    base_url = f"{base_url.rstrip('/')}/{base_path.lstrip('/')}"

        if not base_url:
            return None

        try:
            parsed = urlparse(base_url)
            scheme = parsed.scheme.lower().strip()
            mapped_scheme = scheme
            if scheme == "http":
                mapped_scheme = "ws"
            elif scheme == "https":
                mapped_scheme = "wss"
            elif scheme == "":
                mapped_scheme = "ws"

            path = (parsed.path or "").strip() or "/"
            normalized = parsed._replace(scheme=mapped_scheme, path=path)
            return urlunparse(normalized)
        except Exception:
            return base_url

    @property
    def ws_events_endpoint(self) -> Optional[str]:
        direct_url = str(self.ws_events_url or "").strip()
        if direct_url:
            try:
                parsed = urlparse(direct_url)
                scheme = parsed.scheme.lower().strip()
                mapped_scheme = scheme
                if scheme == "ws":
                    mapped_scheme = "http"
                elif scheme == "wss":
                    mapped_scheme = "https"

                path = (parsed.path or "").strip()
                has_path = path not in {"", "/"}
                resolved_path = path if has_path else f"/{self.ws_event_path.lstrip('/')}"

                normalized = parsed._replace(scheme=mapped_scheme or "http", path=resolved_path)
                return urlunparse(normalized)
            except Exception:
                return direct_url

        base_url = str(self.ws_base_url or "").strip()
        if not base_url:
            host = str(self.ws_host or "").strip()
            if host:
                if "://" in host:
                    base_url = host
                else:
                    protocol = str(self.ws_protocol or "http").strip() or "http"
                    include_port = bool(self.ws_port) and ":" not in host
                    authority = f"{host}:{self.ws_port}" if include_port else host
                    base_url = f"{protocol}://{authority}"

                base_path = str(self.ws_base_path or "").strip()
                if base_path:
                    base_url = f"{base_url.rstrip('/')}/{base_path.lstrip('/')}"

        if not base_url:
            return None

        if "://" in base_url:
            try:
                after_scheme = base_url.split("://", 1)[1]
                host_and_port, _, path_rest = after_scheme.partition("/")
                if host_and_port and path_rest:
                    return base_url
            except Exception:
                pass

        return f"{base_url.rstrip('/')}/{self.ws_event_path.lstrip('/')}"

    @property
    def auth_verify_url(self) -> str:
        return f"{self.app_public_base_url.rstrip('/')}/{self.auth_verify_path.lstrip('/')}"

    @property
    def auth_password_reset_url(self) -> str:
        return f"{self.app_public_base_url.rstrip('/')}/{self.auth_password_reset_path.lstrip('/')}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("mysql://"):
        return database_url.replace("mysql://", "mysql+pymysql://", 1)
    return database_url
