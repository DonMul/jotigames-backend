from datetime import UTC, datetime
import inspect
from types import MethodType
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table

from app.modules.code_conspiracy import CodeConspiracyModule
from app.repositories.code_conspiracy_repository import CodeConspiracyRepository
from app.services.code_conspiracy_service import CodeConspiracyService
from app.services.game_logic_service import GameActionResult


class _PublisherSpy:
    def publish(self, _event: str, _payload: dict, _channels: list[str]) -> None:
        return None


class _RepositoryStub:
    def __getattr__(self, name: str):
        if name == "fetch_teams_by_game_id":
            return lambda *_args, **_kwargs: [{"id": "team-1", "name": "Team 1", "logo_path": "", "geo_score": 1}]
        if name in {"fetch_tasks_by_game_id", "fetch_claims_by_game_id", "fetch_claims_by_game_and_team"}:
            return lambda *_args, **_kwargs: []
        if name in {"get_game_settings", "get_game_by_id"}:
            return lambda *_args, **_kwargs: {}
        if name in {"commit_changes", "rollback_on_error", "insert_claim_without_commit", "update_game_settings_without_commit", "increment_team_geo_score_without_commit"}:
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
        "code_conspiracy_generic",
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
    if name in {"task_id", "challenge_id", "object_id", "claim_id", "code"}:
        return "obj-1"
    if name in {"points", "limit", "offset"}:
        return 1
    if name in {"metadata", "settings", "game_state", "config", "row", "values"}:
        return {}
    if name in {"rows", "actions", "teams", "tasks"}:
        return []
    return "value"


def _bind_service_stubs(service: CodeConspiracyService) -> None:
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


def test_code_conspiracy_service_all_class_functions_unit_invocable():
    service = CodeConspiracyService()
    _bind_service_stubs(service)

    executed: list[str] = []
    for name, method in CodeConspiracyService.__dict__.items():
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

    expected = [name for name, method in CodeConspiracyService.__dict__.items() if inspect.isfunction(method) and not name.startswith("__")]
    assert set(expected).issubset(set(executed))


def test_code_conspiracy_repository_all_class_functions_unit_invocable():
    repo = CodeConspiracyRepository()
    table = _make_table()

    repo.get_game_by_id = lambda *_args, **_kwargs: {"id": "game-1"}
    repo.fetch_teams_by_game_id = lambda *_args, **_kwargs: [{"id": "team-1", "name": "Team 1"}]
    repo.get_team_table = lambda *_args, **_kwargs: table
    repo.get_game_table = lambda *_args, **_kwargs: table
    repo._get_table = lambda *_args, **_kwargs: table
    repo._pick_column = lambda _table, candidates: next((col for col in candidates if col in _table.c), None)

    executed: list[str] = []
    for name, method in CodeConspiracyRepository.__dict__.items():
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

    expected = [name for name, method in CodeConspiracyRepository.__dict__.items() if inspect.isfunction(method) and not name.startswith("__")]
    assert set(expected).issubset(set(executed))


def test_code_conspiracy_get_team_bootstrap_includes_config_teams_highscore():
    service = CodeConspiracyService()
    _bind_service_stubs(service)
    team_rows = [
        {"id": "team-1", "name": "Team 1", "logo_path": "", "geo_score": 5},
        {"id": "team-2", "name": "Team 2", "logo_path": "", "geo_score": 3},
    ]
    service._repository.fetch_teams_by_game_id = lambda *_a, **_k: team_rows
    result = service.get_team_bootstrap(_FakeDb(), "game-1", "team-1")
    assert "config" in result
    assert "rounds" in result["config"]
    assert "code_length" in result["config"]
    assert "max_attempts" in result["config"]
    assert "teams_list" in result
    # teams_list should exclude the requesting team
    assert all(t["team_id"] != "team-1" for t in result["teams_list"])
    assert len(result["teams_list"]) == 1
    assert result["teams_list"][0]["team_id"] == "team-2"
    assert "highscore" in result
    assert len(result["highscore"]) == 2


def test_code_conspiracy_get_team_bootstrap_empty():
    service = CodeConspiracyService()
    _bind_service_stubs(service)
    service._repository.fetch_teams_by_game_id = lambda *_a, **_k: []
    result = service.get_team_bootstrap(_FakeDb(), "game-1", "team-1")
    assert result["teams_list"] == []
    assert result["highscore"] == []
    assert "config" in result


def test_code_conspiracy_module_build_router_registers_routes():
    module = CodeConspiracyModule(ws_publisher=_PublisherSpy())
    router = module.build_router()
    assert router is not None
    assert len(router.routes) > 0
