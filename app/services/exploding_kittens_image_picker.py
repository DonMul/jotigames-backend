import random
from pathlib import Path
from typing import Optional

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}


class ExplodingKittensImagePicker:
    def __init__(self) -> None:
        self._workspace_root = Path(__file__).resolve().parents[3]
        self._cards_root = self._workspace_root / "frontend" / "public" / "uploads" / "cards"

    def listForType(self, card_type: str) -> list[str]:
        type_dir = self._cards_root / card_type
        if not type_dir.exists() or not type_dir.is_dir():
            return []

        files = []
        for entry in sorted(type_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in _ALLOWED_EXTENSIONS:
                continue
            files.append(f"uploads/cards/{card_type}/{entry.name}")
        return files

    def pickRandomForType(self, card_type: str) -> Optional[str]:
        images = self.listForType(card_type)
        if not images:
            return None
        return random.choice(images)
