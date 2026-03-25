import importlib
import inspect
from pathlib import Path

from app.services.game_logic_service import GameActionResult, GameLogicService


def _iter_service_modules():
    services_dir = Path(__file__).resolve().parents[1] / 'app' / 'services'
    for file in sorted(services_dir.glob('*.py')):
        if file.name.startswith('__'):
            continue
        yield f"app.services.{file.stem}"


def _instantiate_if_possible(cls):
    signature = inspect.signature(cls)
    kwargs = {}
    for name, parameter in signature.parameters.items():
        if parameter.default is not inspect._empty:
            continue
        return None
    return cls(**kwargs)


class _FakeRepository:
    def __init__(self):
        self._settings = {}
        self._teams = [{'id': 'team-1', 'name': 'Team 1', 'geo_score': 3}]
        self.last_increment = None
        self.committed = False

    def get_game_settings(self, _db, _game_id):
        return self._settings

    def get_team_by_game_and_id(self, _db, _game_id, _team_id):
        return {'id': 'team-1', 'name': 'Team 1', 'geo_score': 3}

    def fetch_teams_by_game_id(self, _db, _game_id):
        return self._teams

    def increment_team_geo_score_without_commit(self, _db, team_id, points):
        self.last_increment = (team_id, points)

    def update_game_settings_without_commit(self, _db, _game_id, settings):
        self._settings = settings

    def commit_changes(self, _db):
        self.committed = True


def test_all_service_modules_import_and_expose_testable_members():
    for module_name in _iter_service_modules():
        module = importlib.import_module(module_name)
        assert module is not None

        public_members = [
            name for name, member in inspect.getmembers(module)
            if not name.startswith('_') and (inspect.isclass(member) or inspect.isfunction(member))
        ]
        assert public_members, f'{module_name} should expose at least one public class/function'


def test_gamelogicservice_subclasses_are_constructible_or_intentionally_parameterized():
    for module_name in _iter_service_modules():
        module = importlib.import_module(module_name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module_name:
                continue
            if not issubclass(cls, GameLogicService) or cls is GameLogicService:
                continue

            instance = _instantiate_if_possible(cls)
            assert instance is not None, f'{cls.__name__} should be default-constructible for runtime wiring'
            assert isinstance(instance._game_type, str) and instance._game_type


def test_gamelogicservice_base_behaviour_with_fake_repository():
    service = GameLogicService('test_game')
    fake_repository = _FakeRepository()
    service._repository = fake_repository

    bootstrap = service.get_team_bootstrap(None, 'game-1', 'team-1')
    assert bootstrap['team_id'] == 'team-1'
    assert 'score' in bootstrap and 'actions' in bootstrap

    overview = service.get_admin_overview(None, 'game-1')
    assert 'teams' in overview and isinstance(overview['teams'], list)

    result = service.apply_action(
        None,
        game_id='game-1',
        team_id='team-1',
        action_name='test.action',
        object_id='obj-1',
        points_awarded=2,
        allow_repeat=False,
        metadata={'k': 'v'},
        success_message_key='ok',
        already_message_key='already',
    )

    assert isinstance(result, GameActionResult)
    assert result.success is True
    assert fake_repository.last_increment == ('team-1', 2)
    assert fake_repository.committed is True
