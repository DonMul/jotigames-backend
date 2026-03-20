from pathlib import Path
from typing import Dict, List, Tuple

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}


class TeamLogoCatalogService:
    def __init__(self) -> None:
        self._workspace_root = Path(__file__).resolve().parents[3]
        self._uploads_root = self._workspace_root / "frontend" / "public" / "uploads"
        self._team_library_root = self._uploads_root / "team_logo_library"
        self._cards_root = self._uploads_root / "cards"

    @staticmethod
    def _to_human_label(value: str) -> str:
        return value.replace("_", " ").replace("-", " ").strip().title()

    @staticmethod
    def _logo_label(filename: str) -> str:
        return TeamLogoCatalogService._to_human_label(Path(filename).stem)

    def _list_files(self, directory: Path) -> List[Path]:
        if not directory.exists() or not directory.is_dir():
            return []

        files: List[Path] = []
        for entry in sorted(directory.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in _ALLOWED_EXTENSIONS:
                continue
            files.append(entry)
        return files

    def listCatalog(self) -> Tuple[List[Tuple[str, str]], List[Dict[str, str]]]:
        categories: List[Tuple[str, str]] = []
        options: List[Dict[str, str]] = []

        if self._team_library_root.exists() and self._team_library_root.is_dir():
            for category_dir in sorted(self._team_library_root.iterdir()):
                if not category_dir.is_dir():
                    continue

                category_key = f"lib_{category_dir.name}"
                category_label = self._to_human_label(category_dir.name)
                files = self._list_files(category_dir)
                if not files:
                    continue

                categories.append((category_key, category_label))
                for file_path in files:
                    options.append(
                        {
                            "value": f"uploads/team_logo_library/{category_dir.name}/{file_path.name}",
                            "label": self._logo_label(file_path.name),
                            "category": category_key,
                        }
                    )

        if self._cards_root.exists() and self._cards_root.is_dir():
            for category_dir in sorted(self._cards_root.iterdir()):
                if not category_dir.is_dir():
                    continue

                category_key = f"ek_{category_dir.name}"
                category_label = self._to_human_label(category_dir.name)
                files = self._list_files(category_dir)
                if not files:
                    continue

                categories.append((category_key, category_label))
                for file_path in files:
                    options.append(
                        {
                            "value": f"uploads/cards/{category_dir.name}/{file_path.name}",
                            "label": self._logo_label(file_path.name),
                            "category": category_key,
                        }
                    )

        return categories, options
