from app.main import create_app


def _route_prefix(path: str) -> str:
    if not path.startswith('/api/'):
        return path
    parts = path.split('/')
    if len(parts) < 3:
        return path
    return f"/{parts[1]}/{parts[2]}"


def test_openapi_contains_all_expected_api_prefixes():
    app = create_app()
    schema = app.openapi()
    paths = set(schema.get('paths', {}).keys())
    prefixes = {_route_prefix(path) for path in paths if path.startswith('/api/')}

    expected_prefixes = {
        '/api/auth',
        '/api/game',
        '/api/exploding-kittens',
        '/api/geohunter',
        '/api/blindhike',
        '/api/resource-run',
        '/api/territory-control',
        '/api/market-crash',
        '/api/crazy88',
        '/api/courier-rush',
        '/api/echo-hunt',
        '/api/checkpoint-heist',
        '/api/pandemic-response',
        '/api/birds-of-prey',
        '/api/code-conspiracy',
        '/api/super-admin',
        '/api/system',
    }

    missing = sorted(expected_prefixes - prefixes)
    assert not missing, f"Missing API prefixes in OpenAPI schema: {missing}"


def test_every_openapi_operation_has_response_contract():
    app = create_app()
    schema = app.openapi()

    for path, methods in schema.get('paths', {}).items():
        for method, operation in methods.items():
            assert isinstance(operation, dict), f"Operation shape must be object for {method.upper()} {path}"
            responses = operation.get('responses')
            assert isinstance(responses, dict) and responses, f"Missing responses for {method.upper()} {path}"
