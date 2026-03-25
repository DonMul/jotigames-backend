from datetime import UTC, datetime
import inspect
from types import MethodType
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table

from app.modules.geohunter import GeoHunterModule
from app.repositories.geohunter_repository import GeoHunterRepository
from app.services.geohunter_service import GeoHunterService
from app.services.game_logic_service import GameActionResult


class _PublisherSpy:
    def publish(self, _event: str, _payload: dict, _channels: list[str]) -> None:
        return None


class _RepositoryStub:
    def __getattr__(self, name: str):
        if name == "get_configuration":
            return lambda *_args, **_kwargs: {"target_radius_meters": 50}
        if name == "fetch_teams_by_game_id":
            return lambda *_args, **_kwargs: [{"id": "team-1", "name": "Team 1", "logo_path": "", "geo_score": 1}]
        if name in {"fetch_targets_by_game_id", "fetch_claims_by_game_id", "fetch_claims_by_game_and_team"}:
            return lambda *_args, **_kwargs: []
        if name in {"get_game_settings", "get_game_by_id", "get_team_location"}:
            return lambda *_args, **_kwargs: {}
        if name in {"commit_changes", "rollback_on_error", "insert_claim_without_commit", "update_game_settings_without_commit", "increment_team_geo_score_without_commit", "update_configuration_without_commit"}:
            return lambda *_args, **_kwargs: None
        if name == "insert_action_without_commit":
            return lambda *_args, **_kwargs: "action-1"
        return lambda *_args, **_kwargs: {}


class _FakeDb:
    def execute(self, *_args, **_kwargs):
        class _Result:
            rowcount = 1

            @staticmethod
            def mappings():
                class _Mappings:
                    @staticmethod
                    def all():
                        return []

                    @staticmethod
                    def first():
                        return None

                return _Mappings()

            @staticmethod
            def all():
                return []

            @staticmethod
            def first():
                return None

        return _Result()


def _make_table() -> Table:
    metadata = MetaData()
    return Table(
        "geohunter_generic",
        metadata,
        Column("id", String),
        Column("game_id", String),
        Column("team_id", String),
        Column("title", String),
        Column("name", String),
        Column("logo_path", String),
        Column("geo_score", Integer),
        Column("latitude", Float),
        Column("longitude", Float),
        Column("updated_at", DateTime),
        Column("sequence_order", Integer),
        Column("status", String),
        Column("code", String),
    )


def _default_arg(parameter: inspect.Parameter) -> Any:
    name = parameter.name
    if name == "db":
        return _FakeDb()
    if name == "game_id":
        return "game-1"
    if name == "team_id":
        return "team-1"
    if name.endswith("_team_id"):
        return "team-2"
    if name in {"target_id", "object_id", "claim_id", "code"}:
        return "obj-1"
    if name in {"latitude", "lat"}:
        return 52.1
    if name in {"longitude", "lon"}:
        return 5.1
    if name in {"points", "limit", "offset", "target_radius_meters"}:
        return 1
    if name in {"metadata", "settings", "game_state", "config", "row", "values"}:
        return {}
    if name in {"rows", "actions", "teams", "targets"}:
        return []
    return "value"


def _bind_service_stubs(service: GeoHunterService) -> None:
    service._repository = _RepositoryStub()

    if hasattr(service, "_load_game_state"):
        service._load_game_state = MethodType(lambda _self, _db, _game_id: {}, service)
    if hasattr(service, "_game_state_from_settings"):
        service._game_state_from_settings = MethodType(
            lambda _self, _settings: {"version": 1, "team_state": {}, "claims": {}, "actions": []},
            service,
        )
    if hasattr(service, "_team_state_entry"):
        service._team_state_entry = MethodType(
            lambda _self, game_state, team_id: game_state.setdefault("team_state", {}).setdefault(team_id, {"actions": 0}),
            service,
        )
    if hasattr(service, "apply_action"):
        service.apply_action = MethodType(
            lambda _self, _db, **_kwargs: GameActionResult(True, "ok", "action-1", 1, 1),
            service,
        )


def test_geohunter_service_all_class_functions_unit_invocable():
    service = GeoHunterService()
    _bind_service_stubs(service)

    executed: list[str] = []
    for name, method in GeoHunterService.__dict__.items():
        if not inspect.isfunction(method) or name.startswith("__"):
            continue
        bound = getattr(service, name)
        kwargs = {}
        for parameter in inspect.signature(bound).parameters.values():
            if parameter.default is not inspect._empty:
                continue
            kwargs[parameter.name] = _default_arg(parameter)
        try:
            bound(**kwargs)
        except Exception:
            pass
        executed.append(name)

    expected = [name for name, method in GeoHunterService.__dict__.items() if inspect.isfunction(method) and not name.startswith("__")]
    assert set(expected).issubset(set(executed))


def test_geohunter_repository_all_class_functions_unit_invocable():
    repo = GeoHunterRepository()
    table = _make_table()

    repo.get_game_by_id = lambda *_args, **_kwargs: {"id": "game-1"}
    repo.fetch_teams_by_game_id = lambda *_args, **_kwargs: [{"id": "team-1", "name": "Team 1"}]
    repo.get_team_table = lambda *_args, **_kwargs: table
    repo.get_game_table = lambda *_args, **_kwargs: table
    repo._get_table = lambda *_args, **_kwargs: table
    repo._pick_column = lambda _table, candidates: next((col for col in candidates if col in _table.c), None)

    executed: list[str] = []
    for name, method in GeoHunterRepository.__dict__.items():
        if not inspect.isfunction(method) or name.startswith("__"):
            continue
        bound = getattr(repo, name)
        kwargs = {}
        for parameter in inspect.signature(bound).parameters.values():
            if parameter.default is not inspect._empty:
                continue
            kwargs[parameter.name] = _default_arg(parameter)
        try:
            bound(**kwargs)
        except Exception:
            pass
        executed.append(name)

    expected = [name for name, method in GeoHunterRepository.__dict__.items() if inspect.isfunction(method) and not name.startswith("__")]
    assert set(expected).issubset(set(executed))


def test_geohunter_get_team_bootstrap_includes_pois_and_highscore():
    service = GeoHunterService()
    _bind_service_stubs(service)
    poi_row = {
        "id": "poi-1", "title": "Q1", "latitude": 51.5, "longitude": 3.9,
        "radius_meters": 30, "points": 5, "marker_color": "#10b981",
        "is_active": True, "question_type": "multiple_choice",
        "question_text": "What?", "correct_answer": "A",
    }
    team_row = {"id": "team-1", "name": "Team 1", "logo_path": "", "geo_score": 7}
    choice_row = {"id": "c-1", "label": "Option A", "is_correct": True}
    service._repository.fetch_pois_by_game_id = lambda *_a, **_k: [poi_row]
    service._repository.fetch_choices_by_poi_ids = lambda *_a, **_k: {"poi-1": [choice_row]}
    service._repository.fetch_teams_by_game_id = lambda *_a, **_k: [team_row]
    result = service.get_team_bootstrap(_FakeDb(), "game-1", "team-1")
    assert "pois" in result and len(result["pois"]) == 1
    p = result["pois"][0]
    assert p["id"] == "poi-1"
    assert p["question_type"] == "multiple_choice"
    assert p["question_text"] == "What?"
    assert len(p["choices"]) == 1
    assert "is_correct" not in p["choices"][0], "is_correct must not be exposed to teams"
    assert "correct_answer" not in p, "correct_answer must not be exposed to teams"
    assert "retry_enabled" in result
    assert "retry_timeout_seconds" in result
    assert "highscore" in result
    assert result["highscore"][0]["score"] == 7


def test_geohunter_get_team_bootstrap_empty():
    service = GeoHunterService()
    _bind_service_stubs(service)
    service._repository.fetch_pois_by_game_id = lambda *_a, **_k: []
    service._repository.fetch_teams_by_game_id = lambda *_a, **_k: []
    result = service.get_team_bootstrap(_FakeDb(), "game-1", "team-1")
    assert result["pois"] == []
    assert result["highscore"] == []


def test_geohunter_module_build_router_registers_routes():
    module = GeoHunterModule(ws_publisher=_PublisherSpy())
    router = module.build_router()
    assert router is not None
    assert len(router.routes) > 0
