import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / 'app'


def _iter_python_modules(relative_dir: str):
    target = ROOT / relative_dir
    for file in sorted(target.glob('*.py')):
        if file.name.startswith('__'):
            continue
        module_name = f"app.{relative_dir}.{file.stem}"
        yield module_name


def test_all_service_modules_import_cleanly():
    for module_name in _iter_python_modules('services'):
        module = importlib.import_module(module_name)
        assert module is not None


def test_all_repository_modules_import_cleanly():
    for module_name in _iter_python_modules('repositories'):
        module = importlib.import_module(module_name)
        assert module is not None


def test_all_module_router_modules_import_cleanly():
    modules_dir = ROOT / 'modules'
    for file in sorted(modules_dir.glob('*.py')):
        if file.name.startswith('__'):
            continue
        module_name = f"app.modules.{file.stem}"
        module = importlib.import_module(module_name)
        assert module is not None
