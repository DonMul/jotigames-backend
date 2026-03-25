import importlib
import inspect
from pathlib import Path

from app.repositories.game_logic_state_repository import GameLogicStateRepository


def _iter_repository_modules():
    repositories_dir = Path(__file__).resolve().parents[1] / 'app' / 'repositories'
    for file in sorted(repositories_dir.glob('*.py')):
        if file.name.startswith('__'):
            continue
        yield f"app.repositories.{file.stem}"


def _instantiate_if_possible(cls):
    signature = inspect.signature(cls)
    kwargs = {}
    for _, parameter in signature.parameters.items():
        if parameter.default is not inspect._empty:
            continue
        return None
    return cls(**kwargs)


def test_all_repository_modules_import_and_expose_repository_classes():
    discovered = 0
    for module_name in _iter_repository_modules():
        module = importlib.import_module(module_name)
        assert module is not None

        repository_classes = [
            cls for _, cls in inspect.getmembers(module, inspect.isclass)
            if cls.__module__ == module_name and cls.__name__.endswith('Repository')
        ]
        assert repository_classes, f'{module_name} should define at least one Repository class'
        discovered += len(repository_classes)

    assert discovered > 0


def test_repository_classes_are_constructible_with_default_constructor():
    for module_name in _iter_repository_modules():
        module = importlib.import_module(module_name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module_name or not cls.__name__.endswith('Repository'):
                continue
            instance = _instantiate_if_possible(cls)
            assert instance is not None, f'{cls.__name__} should be default-constructible'


def test_game_logic_state_repository_json_deserialization_contract():
    repo = GameLogicStateRepository()

    assert repo._deserialize_json_value({'x': 1}) == {'x': 1}
    assert repo._deserialize_json_value('{"x": 1}') == {'x': 1}
    assert repo._deserialize_json_value("{'x': 1}") == {'x': 1}
    assert repo._deserialize_json_value('') == {}
    assert repo._deserialize_json_value(None) == {}
