# WS Events & Payload Reference (Backend)

This document is the backend-side source of truth for JotiGames realtime event names and payloads.

- Scope: events published by backend business logic via `WsEventPublisher`.
- Transport: backend sends `core.publish` over websocket to `ws/`.
- Ownership: backend owns event semantics and payload shape; `ws/` only transports.

## Publish command contract (`core.publish`)

Backend sends this envelope to WS:

```json
{
  "command": "core.publish",
  "apiKey": "<BACKEND_TO_WS_API_KEY>",
  "event": "<event-name>",
  "payload": {},
  "channels": ["channel:{game_id}:admin"]
}
```

Rules:

- `apiKey` must match `BACKEND_TO_WS_API_KEY` in `ws/` config, otherwise publish is ignored.
- `channels` are explicit targets. Only listed channels are published to.
- Empty `channels` means no recipients in current WS implementation.

## Channel model

- `channel:{game_id}`: game-wide audience.
- `channel:{game_id}:{team_id}`: single team audience.
- `channel:{game_id}:admin`: admin audience for a game.

## Outbound backend events

### Game

#### Blind Hike

##### `game.blind_hike.marker.added`

- Purpose: Broadcast latest marker count for a team to game-wide consumers (e.g. team highscore updates).
- Payload:

```json
{
  "game_id": "<game_id>",
  "team_id": "<team_id>",
  "marker_count": 4,
  "team_finished": true
}
```

#### Exploding Kittens

##### `game.exploding_kittens.highscore.adjust`

- Purpose: Broadcast a team lives adjustment for game-wide highscore updates.
- Payload:

```json
{
  "team_id": "<team_id>",
  "lives": 3
}
```

#### General

##### `game.general.team.update`

- Purpose: Broadcast a team identity/lives update to game-wide consumers (e.g. live highscores).
- Payload:

```json
{
  "team_id": "<team_id>",
  "team_name": "<team-name>",
  "team_logo": "<team-logo-path>",
  "lives": 9
}
```

##### `game.general.team.add`

- Purpose: Broadcast that a team was added to the game.
- Payload:

```json
{
  "team_id": "<team_id>",
  "team_name": "<team-name>",
  "team_logo": "<team-logo-path>",
  "lives": 9
}
```

##### `game.general.team.remove`

- Purpose: Broadcast that a team was removed from the game.
- Payload:

```json
{
  "team_id": "<team_id>",
  "team_name": "<team-name>",
  "team_logo": "<team-logo-path>",
  "lives": 9
}
```

#### Chat

##### `game.chat.message`

- Purpose: Broadcast a newly sent game chat message to realtime consumers.
- Payload:

```json
{
  "id": "<message_id>",
  "gameId": "<game_id>",
  "message": "<text>",
  "sentAt": "<ISO-8601 UTC>",
  "authorRole": "admin|team",
  "authorLabel": "<display-name>",
  "authorTeamId": "<team_id|null>",
  "authorLogoPath": "<path|null>",
  "authorSessionId": "api:<principal_type>:<principal_id>"
}
```

### Admin

#### Blind Hike

##### `admin.blind_hike.marker.added`

- Purpose: Notify admin live overview that a team placed a marker, including marker coordinates and latest team marker count.
- Payload:

```json
{
  "game_id": "<game_id>",
  "team_id": "<team_id>",
  "marker_count": 4,
  "team_finished": true,
  "marker": {
    "id": "<marker_id>",
    "lat": 52.1234567,
    "lon": 5.1234567,
    "placed_at": "<ISO-8601>"
  }
}
```

### Team

#### Blind Hike

##### `team.blind_hike.marker.added`

- Purpose: Notify the team channel that its own marker was added and whether this placement completed the game objective.
- Payload:

```json
{
  "game_id": "<game_id>",
  "team_id": "<team_id>",
  "marker_count": 4,
  "team_finished": true,
  "marker": {
    "id": "<marker_id>",
    "lat": 52.1234567,
    "lon": 5.1234567,
    "placed_at": "<ISO-8601>"
  }
}
```

#### Message

##### `admin.message.team`

- Purpose: Deliver an admin-to-team direct message event.
- Payload:

```json
{
  "teamId": "<team_id>",
  "id": "<message_id>",
  "message": "<text>",
  "level": "info|warning|...",
  "from": "admin",
  "gameId": "<game_id>",
  "createdAt": "<iso-or-datetime-string>"
}
```

#### General

##### `admin.general.team.update`

- Purpose: Notify admin live overview that a team identity/lives entry changed.
- Payload:

```json
{
  "team_id": "<team_id>",
  "team_name": "<team-name>",
  "team_logo": "<team-logo-path>",
  "lives": 9
}
```

##### `admin.general.team.add`

- Purpose: Notify admin live overview that a team was added.
- Payload:

```json
{
  "team_id": "<team_id>",
  "team_name": "<team-name>",
  "team_logo": "<team-logo-path>",
  "lives": 9
}
```

##### `admin.general.team.remove`

- Purpose: Notify admin live overview that a team was removed.
- Payload:

```json
{
  "team_id": "<team_id>",
  "team_name": "<team-name>",
  "team_logo": "<team-logo-path>",
  "lives": 9
}
```

#### Exploding Kittens

##### `admin.exploding_kittens.card.adjust_amount`

- Purpose: Update admin views with a team card-type amount change in Exploding Kittens.
- Payload:

```json
{
  "game_id": "<game_id>",
  "team_id": "<team_id>",
  "card_type": "<card_type>",
  "amount": 3
}
```

##### `admin.exploding_kittens.state.activate`

- Purpose: Notify admin overview that a team activated a pending state flag.
- Payload:

```json
{
  "team_id": "<team_id>",
  "state": "attack|see_the_future|skip"
}
```

##### `admin.exploding_kittens.state.deactivate`

- Purpose: Notify admin overview that a team cleared a pending state flag.
- Payload:

```json
{
  "team_id": "<team_id>",
  "state": "attack|see_the_future|skip"
}
```

##### `admin.exploding_kittens.lives.updated`

- Purpose: Update admin live overview with the latest team lives value.
- Payload:

```json
{
  "team_id": "<team_id>",
  "lives": 3
}
```

##### `admin.exploding_kittens.action.add`

- Purpose: Notify admin live overview that a pending team-targeted EK action was created.
- Payload:

```json
{
  "id": "<action_id>",
  "game_id": "<game_id>",
  "source_team_id": "<source_team_id>",
  "target_team_id": "<target_team_id>",
  "card_id": "<card_id|null>",
  "action_type": "favor|combo_two_same|combo_three_same|attack",
  "status": "pending",
  "created_at": "<datetime>",
  "context": "<json-or-null>"
}
```

##### `admin.exploding_kittens.action.remove`

- Purpose: Notify admin live overview that a pending EK action was removed due to resolve/cancel.
- Payload:

```json
{
  "id": "<action_id>",
  "game_id": "<game_id>",
  "target_team_id": "<target_team_id>",
  "status": "resolved|canceled"
}
```

### Team

#### Blind Hike

##### `team.blind_hike.marker.added`

- Purpose: Notify a team that its marker was placed, with exact marker geolocation and latest marker total for that team.
- Payload:

```json
{
  "game_id": "<game_id>",
  "team_id": "<team_id>",
  "marker_count": 4,
  "marker": {
    "id": "<marker_id>",
    "lat": 52.1234567,
    "lon": 5.1234567,
    "placed_at": "<ISO-8601>"
  }
}
```

#### General

##### `team.general.team.update`

- Purpose: Notify a specific team channel that its team identity/lives were updated.
- Payload:

```json
{
  "team_id": "<team_id>",
  "team_name": "<team-name>",
  "team_logo": "<team-logo-path>",
  "lives": 9
}
```

##### `team.general.message`

- Purpose: Deliver direct popup-style system/admin messages to a single team channel.
- Payload:

```json
{
  "teamId": "<team_id>",
  "id": "<message_id>",
  "message": "<text>",
  "title": "<optional-title>",
  "level": "info|warning|error",
  "from": "admin|system",
  "gameId": "<game_id>",
  "createdAt": "<ISO-8601>"
}
```

#### Exploding Kittens

##### `team.exploding_kittens.card.add`

- Purpose: Notify a team that a card was added to its hand.
- Payload:

```json
{
  "game_id": "<game_id>",
  "team_id": "<team_id>",
  "name": "<card-title>",
  "type": "<card-type>",
  "image": "<image-path>"
}
```

##### `team.exploding_kittens.card.remove`

- Purpose: Notify a team that a card was removed from its hand.
- Payload:

```json
{
  "game_id": "<game_id>",
  "team_id": "<team_id>",
  "id": "<removed-card-id>"
}
```

##### `team.exploding_kittens.state.activate`

- Purpose: Notify a team that one of its pending states became active.
- Payload:

```json
{
  "state": "attack|see_the_future|skip"
}
```

##### `team.exploding_kittens.state.deactivate`

- Purpose: Notify a team that one of its pending states was cleared.
- Payload:

```json
{
  "state": "attack|see_the_future|skip"
}
```

##### `team.exploding_kittens.lives.updated`

- Purpose: Notify a team of its latest lives value.
- Payload:

```json
{
  "lives": 3
}
```

##### `team.exploding_kittens.action.add`

- Purpose: Notify a targeted team that a pending EK action was created and must be accepted/nope-resolved.
- Payload:

```json
{
  "id": "<action_id>",
  "game_id": "<game_id>",
  "source_team_id": "<source_team_id>",
  "target_team_id": "<target_team_id>",
  "card_id": "<card_id|null>",
  "action_type": "favor|combo_two_same|combo_three_same|attack",
  "status": "pending",
  "created_at": "<datetime>",
  "context": "<json-or-null>"
}
```

##### `team.exploding_kittens.action.remove`

- Purpose: Notify a targeted team that a pending EK action row must be removed after resolve/cancel.
- Payload:

```json
{
  "id": "<action_id>",
  "game_id": "<game_id>",
  "target_team_id": "<target_team_id>",
  "status": "resolved|canceled"
}
```

## WS-generated event (transport-level)

### Core

#### Connected

##### `core.connected`

- Purpose: initial connection handshake metadata.
- Payload:

```json
{
  "connectionId": "<uuid>",
  "protocol": "jotigames-wss.v1",
  "now": "<ISO-8601 UTC>"
}
```

## Change policy (mandatory)

When adding/changing backend realtime behavior:

1. Search this document for an existing event that fits.
2. Reuse the existing event name/payload whenever possible.
3. If a new event is required, add it to this document in the same change set.
4. Keep backend emitters, WS transport contract, and frontend consumers synchronized.
