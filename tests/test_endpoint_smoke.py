from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.database import engine


_ALLOWED_STATUS_CODES = {200, 201, 204, 400, 401, 403, 404, 405, 409, 422}


def _example_value(param_name: str) -> str:
    if 'id' in param_name.lower():
        return 'test-id'
    if 'slug' in param_name.lower():
        return 'test-slug'
    if 'token' in param_name.lower():
        return 'test-token'
    return 'test'


def _render_path(path_template: str) -> str:
    rendered = path_template
    while '{' in rendered and '}' in rendered:
        start = rendered.index('{')
        end = rendered.index('}', start)
        token = rendered[start + 1:end]
        rendered = rendered[:start] + _example_value(token) + rendered[end + 1:]
    return rendered


def _request_with_method(client: TestClient, method: str, path: str):
    method = method.lower()
    if method == 'get':
        return client.get(path)
    if method == 'delete':
        return client.delete(path)
    if method == 'post':
        return client.post(path, json={})
    if method == 'put':
        return client.put(path, json={})
    if method == 'patch':
        return client.patch(path, json={})
    if method == 'options':
        return client.options(path)
    if method == 'head':
        return client.head(path)
    raise AssertionError(f'Unsupported HTTP method: {method}')


def _ensure_minimal_schema() -> None:
    with engine.begin() as connection:
        connection.execute(text(
            """
            CREATE TABLE IF NOT EXISTS game_type_availability (
                game_type TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        ))

        connection.execute(text(
            """
            INSERT OR IGNORE INTO game_type_availability (game_type, enabled)
            VALUES ('geohunter', 1)
            """
        ))


def test_all_documented_endpoints_do_not_raise_server_errors_for_minimal_requests():
    _ensure_minimal_schema()
    app = create_app()
    client = TestClient(app)
    schema = app.openapi()

    failures = []

    for raw_path, methods in schema.get('paths', {}).items():
        path = _render_path(raw_path)
        for method in methods.keys():
            response = _request_with_method(client, method, path)
            if response.status_code not in _ALLOWED_STATUS_CODES:
                failures.append((method.upper(), raw_path, response.status_code, response.text[:400]))

    assert not failures, f'Unexpected endpoint statuses: {failures}'
