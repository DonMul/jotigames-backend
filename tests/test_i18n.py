from app.services import i18n


def test_normalize_locale_primary_token_and_fallback():
    assert i18n._normalize_locale("EN_us") == "en"
    assert i18n._normalize_locale("nl-BE") == "nl"


def test_translate_value_with_requested_locale_and_params(monkeypatch):
    monkeypatch.setattr(
        i18n,
        "_load_translations",
        lambda: {
            "locales": {
                "en": {"auth": {"invalid": "Invalid credentials"}},
                "nl": {"auth": {"invalid": "Ongeldige gegevens"}},
            }
        },
    )

    assert i18n.translate_value("auth.invalid", locale="nl") == "Ongeldige gegevens"
    assert i18n.translate_value("auth.invalid", locale="fr") == "Invalid credentials"
    assert i18n.translate_value("missing.key", locale="en") == "missing.key"

    monkeypatch.setattr(
        i18n,
        "_load_translations",
        lambda: {
            "locales": {
                "en": {"mail": {"hello": "Hello {{name}}"}},
            }
        },
    )
    assert i18n.translate_value("mail.hello", locale="en", params={"{{name}}": "Scout"}) == "Hello Scout"
