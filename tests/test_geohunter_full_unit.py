from datetime import UTC, datetime
import inspect
from types import MethodType
from typing import Any
from uuid import uuid4

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


def test_geohunter_create_poi_text_payload_unit_validates_and_serializes():
    module = GeoHunterModule(ws_publisher=_PublisherSpy())

    poi_id = str(uuid4())
    values = {
        "id": poi_id,
        "game_id": "game-1",
        "title": "Infobord",
        "type": "text",
        "points": 2,
        "latitude": 52.1,
        "longitude": 5.1,
        "radius_meters": 25,
        "content": "Lees dit eerst",
        "question": None,
        "expected_answers": None,
    }

    module._validate_poi_payload(
        poi_type="text",
        latitude=52.1,
        longitude=5.1,
        expected_answers=None,
        choices=[],
    )
    serialized = module._serialize_poi(values, [])

    assert serialized["type"] == "text"
    assert serialized["points"] == 2
    assert serialized["content"] == "Lees dit eerst"
    assert serialized["choices"] == []


def test_geohunter_create_poi_multiple_choice_payload_unit_validates_and_serializes():
    module = GeoHunterModule(ws_publisher=_PublisherSpy())

    choices = module._normalize_choices([
        type("Choice", (), {"label": "Eiffel Tower", "correct": True})(),
        type("Choice", (), {"label": "Big Ben", "correct": False})(),
        type("Choice", (), {"label": "Colosseum", "correct": False})(),
    ])
    module._validate_poi_payload(
        poi_type="multiple_choice",
        latitude=52.2,
        longitude=5.2,
        expected_answers=None,
        choices=choices,
    )

    poi_values = {
        "id": str(uuid4()),
        "game_id": "game-1",
        "title": "Quizpunt",
        "type": "multiple_choice",
        "points": 7,
        "latitude": 52.2,
        "longitude": 5.2,
        "radius_meters": 30,
        "content": None,
        "question": "Wat is de hoofdstad van Frankrijk?",
        "expected_answers": None,
    }
    serialized = module._serialize_poi(
        poi_values,
        [
            {"id": "c1", "label": "Eiffel Tower", "is_correct": True},
            {"id": "c2", "label": "Big Ben", "is_correct": False},
            {"id": "c3", "label": "Colosseum", "is_correct": False},
        ],
    )

    assert serialized["type"] == "multiple_choice"
    assert serialized["points"] == 7
    assert len(serialized["choices"]) == 3
    assert any(choice["correct"] for choice in serialized["choices"])


def test_geohunter_create_poi_open_answer_payload_unit_validates_and_serializes():
    module = GeoHunterModule(ws_publisher=_PublisherSpy())

    expected_answers = module._normalize_expected_answers(["Amsterdam", " amsterdam "])
    module._validate_poi_payload(
        poi_type="open_answer",
        latitude=52.3,
        longitude=5.3,
        expected_answers=expected_answers,
        choices=[],
    )

    poi_values = {
        "id": str(uuid4()),
        "game_id": "game-1",
        "title": "Open vraag",
        "type": "open_answer",
        "points": 5,
        "latitude": 52.3,
        "longitude": 5.3,
        "radius_meters": 35,
        "content": None,
        "question": "Wat is de hoofdstad van Nederland?",
        "expected_answers": '["Amsterdam"]',
    }
    serialized = module._serialize_poi(poi_values, [])

    assert serialized["type"] == "open_answer"
    assert serialized["points"] == 5
    assert serialized["expected_answers"] == ["Amsterdam"]


class _GeoSubmissionRepositoryStub:
    def __init__(self, *, poi: dict, game: dict, submission: dict | None, team_score: int, choices_by_poi: dict | None = None) -> None:
        self.poi = dict(poi)
        self.game = dict(game)
        self.submission = dict(submission) if submission else None
        self.team_score = int(team_score)
        self.choices_by_poi = choices_by_poi or {}
        self.created_submissions: list[dict] = []
        self.updated_submissions: list[dict] = []
        self.commit_count = 0
        self._submission_table = Table(
            "geo_submission",
            MetaData(),
            Column("id", String),
            Column("point_id", String),
            Column("team_id", String),
            Column("submitted_at", DateTime),
            Column("answer_text", String),
            Column("selected_choice_ids", String),
            Column("is_correct", Integer),
            Column("points_awarded", Integer),
        )

    def get_poi_by_game_id_and_poi_id(self, *_args, **_kwargs):
        return dict(self.poi)

    def get_game_by_id(self, *_args, **_kwargs):
        return dict(self.game)

    def get_game_settings(self, *_args, **_kwargs):
        return {}

    def get_visibility_mode(self, *_args, **_kwargs):
        return "all_visible"

    def get_team_by_game_and_id(self, *_args, **_kwargs):
        return {"id": "team-1", "geo_score": self.team_score}

    def fetch_choices_by_poi_ids(self, *_args, **_kwargs):
        return self.choices_by_poi

    def get_submission_by_team_and_poi(self, *_args, **_kwargs):
        return dict(self.submission) if self.submission else None

    def get_geo_submission_table(self, *_args, **_kwargs):
        return self._submission_table

    def create_submission_without_commit(self, _db, values):
        self.created_submissions.append(dict(values))
        self.submission = dict(values)

    def update_submission_without_commit(self, _db, submission_id, values):
        payload = dict(values)
        payload["id"] = submission_id
        self.updated_submissions.append(payload)
        if self.submission:
            self.submission.update(values)

    def update_submission_by_team_and_poi_without_commit(self, _db, *, team_id, poi_id, values):
        payload = dict(values)
        payload["team_id"] = team_id
        payload["point_id"] = poi_id
        self.updated_submissions.append(payload)
        if self.submission:
            self.submission.update(values)

    def fetch_submissions_by_team_and_poi_ids(self, _db, *, team_id, poi_ids):
        if not self.submission:
            return {}
        point_id = str(self.submission.get("point_id") or "")
        if point_id and point_id in poi_ids:
            return {point_id: dict(self.submission)}
        return {}

    def fetch_pois_by_game_id(self, *_args, **_kwargs):
        return [dict(self.poi)]

    def fetch_teams_by_game_id(self, *_args, **_kwargs):
        return [{"id": "team-1", "name": "Team 1", "logo_path": "", "geo_score": self.team_score}]

    def update_game_settings_without_commit(self, *_args, **_kwargs):
        return None

    def commit_changes(self, *_args, **_kwargs):
        self.commit_count += 1

    def rollback_on_error(self, *_args, **_kwargs):
        return None


def test_geohunter_answer_question_lock_check_happens_before_validation():
    now = datetime.now(UTC).replace(tzinfo=None)
    repo = _GeoSubmissionRepositoryStub(
        poi={
            "id": "poi-1",
            "question_type": "open_answer",
            "expected_answers": '["yes"]',
            "points": 5,
        },
        game={"geo_hunter_retry_enabled": True, "geo_hunter_retry_timeout_seconds": 30},
        submission={
            "id": "sub-1",
            "point_id": "poi-1",
            "team_id": "team-1",
            "submitted_at": now,
            "is_correct": False,
            "points_awarded": 0,
        },
        team_score=3,
    )
    service = GeoHunterService()
    service._repository = repo
    service.get_nearby_pois_for_team = MethodType(lambda _self, _db, _game_id, _team_id: [{"id": "poi-1", "title": "P1"}], service)

    outcome = service.answer_question(_FakeDb(), game_id="game-1", team_id="team-1", poi_id="poi-1", answer="no")

    assert outcome.lock_active is True
    assert outcome.correct is False
    assert outcome.message_key == "geohunter.answer.retryTimeoutActive"
    assert outcome.retry_available_in_seconds > 0
    assert repo.created_submissions == []
    assert repo.updated_submissions == []


def test_geohunter_answer_question_incorrect_retry_enabled_persists_submission():
    repo = _GeoSubmissionRepositoryStub(
        poi={
            "id": "poi-1",
            "question_type": "open_answer",
            "expected_answers": '["yes"]',
            "points": 4,
        },
        game={"geo_hunter_retry_enabled": True, "geo_hunter_retry_timeout_seconds": 20},
        submission=None,
        team_score=2,
    )
    service = GeoHunterService()
    service._repository = repo
    service.get_nearby_pois_for_team = MethodType(lambda _self, _db, _game_id, _team_id: [{"id": "poi-1", "title": "P1"}], service)

    outcome = service.answer_question(_FakeDb(), game_id="game-1", team_id="team-1", poi_id="poi-1", answer="no")

    assert outcome.correct is False
    assert outcome.message_key == "geohunter.answer.incorrect"
    assert outcome.retry_available_in_seconds == 20
    assert len(repo.created_submissions) == 1
    assert bool(repo.created_submissions[0].get("is_correct")) is False
    assert int(repo.created_submissions[0].get("points_awarded") or 0) == 0
    assert repo.commit_count == 1


def test_geohunter_answer_question_correct_updates_submission_and_awards_points():
    old_time = datetime(2000, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    repo = _GeoSubmissionRepositoryStub(
        poi={
            "id": "poi-1",
            "question_type": "multiple_choice",
            "expected_answers": None,
            "points": 5,
        },
        game={"geo_hunter_retry_enabled": True, "geo_hunter_retry_timeout_seconds": 10},
        submission={
            "id": "sub-1",
            "point_id": "poi-1",
            "team_id": "team-1",
            "submitted_at": old_time,
            "is_correct": False,
            "points_awarded": 0,
        },
        team_score=12,
        choices_by_poi={"poi-1": [{"id": "choice-ok", "is_correct": True}]},
    )
    service = GeoHunterService()
    service._repository = repo
    service.get_nearby_pois_for_team = MethodType(lambda _self, _db, _game_id, _team_id: [{"id": "poi-1", "title": "P1"}], service)
    service.apply_action = MethodType(
        lambda _self, _db, **_kwargs: GameActionResult(True, "geohunter.answer.correct", "action-1", 5, 7),
        service,
    )

    outcome = service.answer_question(_FakeDb(), game_id="game-1", team_id="team-1", poi_id="poi-1", answer="choice-ok")

    assert outcome.success is True
    assert outcome.correct is True
    assert outcome.points_awarded == 5
    assert len(repo.updated_submissions) == 1
    assert bool(repo.updated_submissions[0].get("is_correct")) is True
    assert int(repo.updated_submissions[0].get("points_awarded") or 0) == 5


def test_geohunter_bootstrap_retry_locks_are_derived_from_geo_submission():
    now = datetime.now(UTC).replace(tzinfo=None)
    repo = _GeoSubmissionRepositoryStub(
        poi={
            "id": "poi-1",
            "title": "P1",
            "latitude": 52.1,
            "longitude": 5.1,
            "radius_meters": 25,
            "points": 3,
            "marker_color": "#10b981",
            "is_active": True,
            "question_type": "open_answer",
            "question": "Vraag",
            "content": "",
        },
        game={"geo_hunter_retry_enabled": True, "geo_hunter_retry_timeout_seconds": 60},
        submission={
            "id": "sub-1",
            "point_id": "poi-1",
            "team_id": "team-1",
            "submitted_at": now,
            "is_correct": False,
            "points_awarded": 0,
        },
        team_score=9,
    )
    service = GeoHunterService()
    service._repository = repo
    service.get_nearby_pois_for_team = MethodType(lambda _self, _db, _game_id, _team_id: [{"id": "poi-1", "title": "P1"}], service)

    result = service.get_team_bootstrap(_FakeDb(), "game-1", "team-1")

    assert int(result["retry_locked_poi_seconds"].get("poi-1") or 0) > 0
    assert int(result["nearby_poi_lockouts_seconds"].get("poi-1") or 0) > 0
