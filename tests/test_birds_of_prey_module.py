from app.modules.birds_of_prey import BirdsOfPreyModule


class _PublisherSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, list[str]]] = []

    def publish(self, event: str, payload: dict, channels: list[str]) -> None:
        self.calls.append((event, payload, channels))


def test_build_egg_event_payload_normalizes_all_supported_fields():
    payload = BirdsOfPreyModule._build_egg_event_payload(
        "game-1",
        {
            "id": "egg-1",
            "owner_team_id": "team-1",
            "owner_team_name": "Raptors",
            "lat": 52.1,
            "lon": 5.1,
            "dropped_at": "2026-03-25T10:00:00Z",
            "automatic": True,
        },
    )

    assert payload["game_id"] == "game-1"
    assert payload["id"] == "egg-1"
    assert payload["owner_team_id"] == "team-1"
    assert payload["owner_team_name"] == "Raptors"
    assert payload["lat"] == 52.1
    assert payload["lon"] == 5.1
    assert payload["dropped_at"] == "2026-03-25T10:00:00Z"
    assert payload["automatic"] is True


def test_publish_team_score_event_emits_shared_admin_and_team_channels():
    publisher = _PublisherSpy()
    module = BirdsOfPreyModule(ws_publisher=publisher)

    module._publish_team_score_event(game_id="game-1", team_id="team-1", score=7)

    assert len(publisher.calls) == 3
    assert publisher.calls[0][0] == "game.birds_of_prey.team.score"
    assert publisher.calls[0][2] == ["channel:game-1"]
    assert publisher.calls[1][0] == "admin.birds_of_prey.team.score"
    assert publisher.calls[1][2] == ["channel:game-1:admin"]
    assert publisher.calls[2][0] == "team.birds_of_prey.self.updated"
    assert publisher.calls[2][2] == ["channel:game-1:team-1"]


def test_publish_enemy_visibility_snapshot_emits_team_scoped_event():
    publisher = _PublisherSpy()
    module = BirdsOfPreyModule(ws_publisher=publisher)

    eggs = [{"id": "egg-1", "owner_team_id": "team-2"}]
    module._publish_enemy_visibility_snapshot(game_id="game-1", team_id="team-1", visible_enemy_eggs=eggs)

    assert len(publisher.calls) == 1
    event, payload, channels = publisher.calls[0]
    assert event == "team.birds_of_prey.enemy_eggs.visible"
    assert payload["game_id"] == "game-1"
    assert payload["team_id"] == "team-1"
    assert payload["eggs"] == eggs
    assert channels == ["channel:game-1:team-1"]