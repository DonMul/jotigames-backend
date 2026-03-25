from app.config import Settings


def test_ws_socket_endpoint_mapping_variants():
    settings = Settings(DATABASE_URL='sqlite:///./test.db', WS_EVENTS_URL='https://example.com/admin/events')
    assert settings.ws_socket_endpoint == 'wss://example.com/admin/events'

    settings = Settings(DATABASE_URL='sqlite:///./test.db', WS_EVENTS_URL='http://example.com/admin/events')
    assert settings.ws_socket_endpoint == 'ws://example.com/admin/events'


def test_ws_events_endpoint_from_base_url():
    settings = Settings(
        DATABASE_URL='sqlite:///./test.db',
        WS_BASE_URL='http://localhost:8081',
        WS_EVENT_PATH='/admin/events',
    )
    assert settings.ws_events_endpoint == 'http://localhost:8081/admin/events'


def test_auth_urls_are_built_from_public_base_url():
    settings = Settings(
        DATABASE_URL='sqlite:///./test.db',
        APP_PUBLIC_BASE_URL='https://jotigames.example',
        AUTH_VERIFY_PATH='/api/auth/verify',
        AUTH_PASSWORD_RESET_PATH='/reset-password',
    )
    assert settings.auth_verify_url == 'https://jotigames.example/api/auth/verify'
    assert settings.auth_password_reset_url == 'https://jotigames.example/reset-password'
