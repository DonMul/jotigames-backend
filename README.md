# JotiGames Backend (FastAPI)

This directory contains the Python backend for JotiGames.

## Documentation Note

Cross-system architecture, WS contracts, game flows, and coding decisions are centrally documented in `docs/` at repository root.

Start at: `docs/README.md`

## Features

- FastAPI service with central module registration
- Authentication module with temporary bearer tokens
- Protected endpoints using bearer token checks
- SQLAlchemy integration with the same database used by the frontend
- Alembic migrations for database changes
- Outbound WS action event publishing using WS admin API key
- SSL certificate and key path configuration
- WS model: pub/sub transport only; game business logic stays in backend API

## Quick start

1. Create and activate a Python environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in values.
4. Run migrations:

```bash
python scripts/setup_database.py
```

5. Start API:

```bash
python run.py
```

## Module architecture

Modules are registered in the central controller (`app/controller.py`).
Each module contributes its own `APIRouter` and endpoints.

## Game module

- `GET /api/game/game-types` returns enabled game types from `game_type_availability` (`enabled = 1`).
- CRUD endpoints for `game` table:
  - `GET /api/game`
  - `GET /api/game/{game_id}`
  - `POST /api/game`
  - `PUT /api/game/{game_id}`
  - `DELETE /api/game/{game_id}`
  - `POST /api/game/{game_id}/reset`
- Membership endpoints:
  - `GET /api/game/{game_id}/members`
  - `POST /api/game/{game_id}/admins`
  - `DELETE /api/game/{game_id}/admins/{user_id}`
  - `POST /api/game/{game_id}/game-masters`
  - `DELETE /api/game/{game_id}/game-masters/{user_id}`
- Access model:
  - Owner + game admins (`game_manager`) have read/write access.
  - Game masters (`game_master`) have read-only access.
  - `GET /api/game/{game_id}/members` returns users and merged role labels per game (`owner`, `admin`, `game_master`).
  - `GET /api/game` returns games where the current user is owner/admin/game master.
  - `DELETE /api/game/{game_id}/admins/{user_id}` blocks removing the owner and blocks removing yourself.
- Team endpoints:
  - `GET /api/game/{game_id}/teams`
  - `GET /api/game/{game_id}/teams/{team_id}`
  - `POST /api/game/{game_id}/teams`
  - `PUT /api/game/{game_id}/teams/{team_id}`
  - `DELETE /api/game/{game_id}/teams/{team_id}`
  - `POST /api/game/{game_id}/teams/{team_id}/message` (admin -> team message)
  - Team tokens can read/update only their own team via `GET/PUT /api/game/{game_id}/teams/{team_id}`.
  - Team tokens cannot create or delete teams.
  - Game chat endpoints (all game types):
    - `GET /api/game/{game_id}/chat?limit=50`
    - `POST /api/game/{game_id}/chat`
  - Chat access:
    - Teams can read/send chat for their own game only.
    - Owner/admin users can read/send chat for managed games.
  - Reset endpoint calls game-type-specific reset logic and then publishes `game.reset` to WS.
- Required fields when creating/updating a complete game state:
  - `name`, `code`, `start_at`, `end_at`, `game_type`
- Additional game-type-specific settings can be provided via `settings` payload keys matching `game` table columns.
- Game creation runs game-type initialization logic; for `exploding_kittens` a 115-card deck is inserted automatically.

## Exploding Kittens module

- Base path: `/api/exploding-kittens`
- Card management endpoints (owner/admin):
  - `POST /games/{game_id}/cards/bulk-add`
  - `GET /games/{game_id}/cards`
  - `GET /games/{game_id}/cards/{card_id}`
  - `POST /games/{game_id}/cards`
  - `PUT /games/{game_id}/cards/{card_id}`
  - `DELETE /games/{game_id}/cards/{card_id}`
- Team gameplay endpoints (team self token or owner/admin):
  - `GET /games/{game_id}/teams/{team_id}/state`
  - `POST /games/{game_id}/teams/{team_id}/cards/{card_id}/play`
  - `POST /games/{game_id}/teams/{team_id}/scan`
  - `POST /games/{game_id}/teams/{team_id}/state/resolve`
  - `POST /games/{game_id}/teams/{team_id}/actions/{action_id}/resolve`
  - `POST /games/{game_id}/teams/{team_id}/combos/use`
- Admin EK pending actions endpoint:
  - `GET /games/{game_id}/actions/pending`
- Admin-only EK endpoint:
  - `POST /games/{game_id}/teams/{team_id}/lives/adjust` (body: `{ "delta": <int> }`, positive adds, negative removes, minimum lives is 0)
  - `POST /games/{game_id}/teams/{team_id}/hand/remove-random` (body: `{ "card_type": "..." }`, removes one random card of that type from hand when available)
- Response payloads are endpoint-specific:
  - `GET /state` returns state only.
  - `POST /cards/{card_id}/play` returns only `{ "success", "message_key", "action_type" }`.
  - `POST /scan` returns only `{ "success", "status", "message_key", optional "card", optional "pending_state" }`.
  - `POST /state/resolve` returns only `{ "success", "status", "message_key", optional "pending_state" }`.
  - `POST /actions/{action_id}/resolve` returns only `{ "success", "status", "message_key", optional "card_type" }`.
  - `POST /combos/use` returns only `{ "success", "combo_type" }`.
  - `POST /lives/adjust` returns only `{ "team_id", "lives" }`.
  - `POST /hand/remove-random` returns only `{ "team_id", "card_type", "removed", optional "card_id" }`.
- Scan logic includes: add-to-hand cards, Felix (+1 life), exploding kitten/defuse behavior, and pending state handling (`attack`, `see_the_future`, `skip`).
- Combo support includes: 2-of-a-kind, 3-of-a-kind (requested type), and 5-different (requested type).

### Exploding Kittens auto-resolve worker

- Script: `scripts/auto_resolve_pending_actions.py`
- Behavior:
  - Infinite loop.
  - Resolves one pending action older than 30 seconds as accepted.
  - Sleeps 1 second only when no stale pending action is available.
- Run from backend root:

```bash
PYTHONPATH=. python scripts/auto_resolve_pending_actions.py
```

## Additional game logic modules

- Common pattern for all modules below:
  - Team bootstrap: `GET /api/<module>/{game_id}/teams/{team_id}/bootstrap`
  - Admin overview: `GET /api/<module>/{game_id}/overview`
  - Team gameplay endpoints allow team self token or game manage access.
  - Admin overview and admin-only actions require game manage access.
- Geohunter (`/api/geohunter`):
  - `POST /{game_id}/teams/{team_id}/question/answer`
- Blindhike (`/api/blindhike`):
  - `POST /{game_id}/teams/{team_id}/marker/add`
- Resource Run (`/api/resource-run`):
  - `POST /{game_id}/teams/{team_id}/resource/claim`
- Territory Control (`/api/territory-control`):
  - `POST /{game_id}/teams/{team_id}/zone/claim`
- Market Crash (`/api/market-crash`):
  - `POST /{game_id}/teams/{team_id}/trade/execute`
- Crazy 88 (`/api/crazy88`, game_type `crazy_88`):
  - `POST /{game_id}/teams/{team_id}/task/submit`
  - `POST /{game_id}/review/judge` (admin)
- Courier Rush (`/api/courier-rush`):
  - `POST /{game_id}/teams/{team_id}/pickup/confirm`
  - `POST /{game_id}/teams/{team_id}/dropoff/confirm`
- Echo Hunt (`/api/echo-hunt`):
  - `POST /{game_id}/teams/{team_id}/beacon/claim`
- Checkpoint Heist (`/api/checkpoint-heist`):
  - `POST /{game_id}/teams/{team_id}/capture/confirm`
- Pandemic Response (`/api/pandemic-response`):
  - `POST /{game_id}/teams/{team_id}/pickup/collect`
  - `POST /{game_id}/teams/{team_id}/hotspot/resolve`
- Birds of Prey (`/api/birds-of-prey`):
  - `POST /{game_id}/teams/{team_id}/egg/drop`
  - `POST /{game_id}/teams/{team_id}/egg/destroy`
- Code Conspiracy (`/api/code-conspiracy`):
  - `POST /{game_id}/teams/{team_id}/code/submit`

## Authentication

- User endpoint: `POST /api/auth/user`
- Team endpoint: `POST /api/auth/team`
- Registration endpoint: `POST /api/auth/register`
- Forgot password endpoint: `POST /api/auth/password/forgot`
- Verify endpoints: `POST /api/auth/verify` and `GET /api/auth/verify?token=...`
- WS token verification endpoint: `POST /api/auth/token/verify-access`
- User input: email + password
- Team input: `game_code` + `team_code` (resolved via `game.code` + `team.code`)
- Output: temporary bearer token linked to `principal_type` (`user` or `team`) and `principal_id`
- User role source: `user.roles` column (array/json)
- Access levels:
  - `ROLE_SUPER_ADMIN` -> `super_admin`
  - `ROLE_USER` -> `user`
  - team login -> `team`
- User login requires one of: `ROLE_USER` or `ROLE_SUPER_ADMIN`
- Password verification supports Symfony password hasher output (`auto`, including Argon2id and bcrypt)
- Default token lifetime: 30 days (`TOKEN_TTL_MINUTES=43200`, configurable)
- Protected endpoints accept headers:
  - `Authorization: Bearer <token>`
  - `Authentication: Bearer <token>`
  - `X-Locale: <locale>` (preferred locale for translated response `message` values)
  - `Accept-Language: <locale>` (fallback when `X-Locale` is not provided)

### WS token verification

- `POST /api/auth/token/verify-access` validates whether an auth token may access a given game.
- Request body:
  - `game_id`
  - `auth_token`
- Required header for this endpoint:
  - `X-WS-Super-Admin-Key: <ws-super-admin-key>`
  - Backward compatibility: `X-Admin-Api-Key` or `Authorization: Bearer <key>` is also accepted.
  - This key must match backend env `WS_TO_BACKEND_API_KEY`.
- Response includes:
  - `principal_type` (`user` or `team`)
  - `principal_id`
  - `access_level`
  - `game_id`
  - `channel_game` (`channel:{game_id}`)
  - `channel_target` (`channel:{game_id}:admin` for users, `channel:{game_id}:{team_id}` for teams)

### WS architecture contract

- WS should be transport only (pub/sub), with no business-rule ownership.
- Use two distinct directional keys:
  - `WS_TO_BACKEND_API_KEY`: WS -> backend (`/api/auth/token/verify-access`).
  - `BACKEND_TO_WS_API_KEY`: backend -> WS (`core.publish` over websocket).
- WS outbound endpoint config:
  - Preferred: set `WS_EVENTS_URL` to the backend->WS websocket URL (example: `ws://localhost:8081/`).
  - Backward-compatible alternatives: `WS_BASE_URL` or host parts (`WS_PROTOCOL`, `WS_HOST`, `WS_PORT`, `WS_BASE_PATH`) combined with `WS_EVENT_PATH`.
- Channel model:
  - per game: `channel:{game_id}`
  - per team: `channel:{game_id}:{team_id}`
  - admin: `channel:{game_id}:admin`
- Event publishing must originate from backend API logic and be authenticated with `BACKEND_TO_WS_API_KEY`.
- Canonical realtime event/payload catalog (must be kept in sync): `backend/docs/ws-events-reference.md`.
- Before introducing a new realtime event, reuse an existing documented event when possible; if not possible, add the new event to `backend/docs/ws-events-reference.md` in the same change set.

### Swagger auth testing

- OpenAPI/Swagger now exposes Bearer auth on protected endpoints.
- Use the **Authorize** button and paste only the token value; Swagger sends `Authorization: Bearer <token>` automatically.

### Registration + verification

- Registration requires `email`, `username`, and `password`.
- New users are created as unverified and receive an email verification token.
- Verification email locale is resolved from `X-Locale`/`Accept-Language` (or optional body `locale`) with supported values `en` and `nl` (default: `en`).
- Verification endpoint confirms token and marks user as verified.

### Forgot password mail

- `/api/auth/password/forgot` accepts `email`; locale is resolved from `X-Locale`/`Accept-Language` (or optional body `locale`) using `en`/`nl` (default `en`).
- If account exists and is verified, a localized reset email is sent.
- Response remains generic to avoid account enumeration.

### Error message keys

- API error `detail` values return translation keys only (no user-facing text literals).
- API now also returns a localized `message` field for string-key errors using request locale headers.
- API responses that include `message_key` also automatically include a localized `message` field based on `X-Locale`/`Accept-Language`.
- Request validation errors return `validation.invalidRequest`.
- Translation key mapping is stored per-locale in [translations/locales/en.yaml](translations/locales/en.yaml) and [translations/locales/nl.yaml](translations/locales/nl.yaml).
- `TRANSLATIONS_DIR` controls the locale file directory.

### Mailer configuration

- `MAILER_DSN` configures SMTP transport (`smtp://` or `smtps://`).
- `MAILER_FROM` configures sender address.
- `APP_PUBLIC_BASE_URL` + `AUTH_VERIFY_PATH` are used to build the verification link in email.

### Email templates

- Email bodies are stored in separate template files under [app/templates/emails](app/templates/emails).
- Current templates:
  - [verify_email.html.twig](app/templates/emails/verify_email.html.twig)
  - [verify_email.txt.twig](app/templates/emails/verify_email.txt.twig)
  - [reset_password_email.html.twig](app/templates/emails/reset_password_email.html.twig)
  - [reset_password_email.txt.twig](app/templates/emails/reset_password_email.txt.twig)

### User table mapping

The backend reads users from the configured user table (default `user`) using:

- `AUTH_USERS_TABLE=user`
- `AUTH_USER_ID_COLUMN=id`
- `AUTH_USERNAME_COLUMN=email`
- `AUTH_PASSWORD_COLUMN=password`
- `AUTH_USER_ROLES_COLUMN=roles`

### Team/game table mapping

Team auth uses a join between team and game tables:

- `AUTH_TEAMS_TABLE=team`
- `AUTH_TEAM_ID_COLUMN=id`
- `AUTH_TEAM_CODE_COLUMN=code`
- `AUTH_TEAM_GAME_ID_COLUMN=game_id`
- `AUTH_TEAM_NAME_COLUMN=name`
- `AUTH_GAMES_TABLE=game`
- `AUTH_GAME_ID_COLUMN=id`
- `AUTH_GAME_CODE_COLUMN=code`

## Migrations workflow

Create a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe change"
```

Apply migrations:

```bash
alembic upgrade head
```

## SSL configuration

Set these in `.env`:

- `SSL_CERTFILE`
- `SSL_KEYFILE`
- optional: `SSL_KEYFILE_PASSWORD`

When set, `run.py` starts Uvicorn with TLS enabled.
