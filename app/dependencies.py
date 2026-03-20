from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db_session
from app.config import get_settings
from app.security import AuthenticatedPrincipal, resolve_token_principal


DbSession = Annotated[Session, Depends(get_db_session)]
bearerAuthScheme = HTTPBearer(auto_error=False)


def _extract_bearer_token(
    request: Request,
    authorization: Optional[HTTPAuthorizationCredentials],
) -> str:
    if authorization and authorization.scheme.lower() == "bearer":
        return authorization.credentials

    auth_header = request.headers.get("Authentication")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="auth.token.missingBearerToken",
        )

    parts = auth_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]

    if len(parts) == 3 and parts[0].lower() == "authentication" and parts[1].lower() == "bearer":
        return parts[2]

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="auth.token.invalidBearerHeaderFormat",
    )


def require_authenticated_principal(
    request: Request,
    authorization: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearerAuthScheme)],
    db: DbSession,
) -> AuthenticatedPrincipal:
    token = _extract_bearer_token(request, authorization)
    principal = resolve_token_principal(db, token)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="auth.token.invalidOrExpired",
        )
    return principal


CurrentPrincipal = Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)]


def require_super_admin(principal: CurrentPrincipal) -> AuthenticatedPrincipal:
    if principal.principal_type != "user" or not principal.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="auth.user.superAdminRequired",
        )
    return principal


CurrentSuperAdmin = Annotated[AuthenticatedPrincipal, Depends(require_super_admin)]


def _normalize_locale_candidate(raw_value: Optional[str]) -> Optional[str]:
    if raw_value is None:
        return None

    value = raw_value.strip()
    if not value:
        return None

    primary = value.split(",", 1)[0].split(";", 1)[0].strip()
    if not primary:
        return None

    normalized = primary.replace("_", "-").lower()
    if not normalized:
        return None

    return normalized.split("-", 1)[0]


def resolve_request_locale(request: Request) -> str:
    settings = get_settings()

    for header_name in ("X-Locale", "X-Language", "Accept-Language"):
        normalized = _normalize_locale_candidate(request.headers.get(header_name))
        if normalized:
            return normalized

    return settings.default_locale


CurrentLocale = Annotated[str, Depends(resolve_request_locale)]
