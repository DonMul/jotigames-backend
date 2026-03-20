from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from app.config import get_settings


@lru_cache(maxsize=1)
def _load_translations() -> dict[str, Any]:
    settings = get_settings()
    locales: dict[str, Any] = {}

    if settings.translations_dir:
        dir_path = Path(settings.translations_dir)
        if dir_path.exists() and dir_path.is_dir():
            for locale_file in sorted(dir_path.glob("*.yaml")):
                with locale_file.open("r", encoding="utf-8") as handle:
                    content = yaml.safe_load(handle) or {}
                if isinstance(content, dict):
                    locale = _normalize_locale(locale_file.stem)
                    locales[locale] = content

    if locales:
        return {"locales": locales}

    return {}


def _normalize_locale(locale: Optional[str]) -> str:
    settings = get_settings()
    if not locale:
        return settings.default_locale
    normalized = locale.strip().replace("_", "-").lower()
    if not normalized:
        return settings.default_locale
    return normalized.split("-")[0]


def translate_value(key: str, locale: Optional[str] = None, params: Optional[Dict[str, str]] = None) -> str:
    translations = _load_translations()
    localized = _normalize_locale(locale)
    fallback_locale = get_settings().default_locale

    node: Any = translations.get("locales", {})
    for part in [localized, *key.split(".")]:
        if not isinstance(node, dict) or part not in node:
            node = None
            break
        node = node[part]

    if not isinstance(node, str):
        node = translations.get("locales", {})
        for part in [fallback_locale, *key.split(".")]:
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]

    if not isinstance(node, str):
        return key

    rendered = node
    if params:
        for token, value in params.items():
            rendered = rendered.replace(token, value)
    return rendered
