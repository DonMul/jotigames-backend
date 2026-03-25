import inspect

from app.modules import __all__ as module_exports
from app.modules import __dict__ as module_namespace
from app.modules.base import ApiModule
from app.services.ws_client import WsEventPublisher


def _build_instance(cls):
    signature = inspect.signature(cls)
    kwargs = {}
    for name, parameter in signature.parameters.items():
        if parameter.default is not inspect._empty:
            continue
        if name == 'ws_publisher':
            kwargs[name] = WsEventPublisher()
            continue
        raise AssertionError(f'Unsupported required constructor arg {name} for {cls.__name__}')
    return cls(**kwargs)


def test_every_api_module_builds_router_with_routes():
    module_classes = [
        module_namespace[name]
        for name in module_exports
        if name.endswith('Module') and isinstance(module_namespace.get(name), type)
    ]

    assert module_classes, 'No ApiModule classes discovered in app.modules export list'

    for cls in module_classes:
        assert issubclass(cls, ApiModule), f'{cls.__name__} must extend ApiModule'
        instance = _build_instance(cls)
        router = instance.build_router()
        assert router is not None
        assert len(router.routes) > 0, f'{cls.__name__} should register at least one route'
