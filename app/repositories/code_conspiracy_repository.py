from typing import Any, Dict

from datetime import UTC, datetime

from sqlalchemy import update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class CodeConspiracyRepository(GameLogicStateRepository):
    """Repository helpers for Code Conspiracy configuration and outcome writes."""

    @staticmethod
    def _first_present(row: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
        """Return the first existing key from a row among candidate names."""
        for key in keys:
            if key in row:
                return row.get(key)
        return default

    def get_configuration(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        """Load Code Conspiracy settings from game columns with fallback defaults."""
        game = self.get_game_by_id(db, game_id)
        if game is None:
            return {}

        return {
            "code_length": int(self._first_present(game, ["code_conspiracy_code_length", "codeConspiracyCodeLength"], 6) or 6),
            "character_set": str(self._first_present(game, ["code_conspiracy_character_set", "codeConspiracyCharacterSet"], "alphanumeric") or "alphanumeric"),
            "submission_cooldown_seconds": int(self._first_present(game, ["code_conspiracy_submission_cooldown_seconds", "codeConspiracySubmissionCooldownSeconds"], 0) or 0),
            "correct_points": int(self._first_present(game, ["code_conspiracy_correct_points", "codeConspiracyCorrectPoints"], 10) or 10),
            "penalty_enabled": bool(self._first_present(game, ["code_conspiracy_penalty_enabled", "codeConspiracyPenaltyEnabled"], False)),
            "penalty_value": int(self._first_present(game, ["code_conspiracy_penalty_value", "codeConspiracyPenaltyValue"], 0) or 0),
            "first_bonus_enabled": bool(self._first_present(game, ["code_conspiracy_first_correct_bonus_enabled", "codeConspiracyFirstCorrectBonusEnabled"], False)),
            "first_bonus_points": int(self._first_present(game, ["code_conspiracy_first_correct_bonus_points", "codeConspiracyFirstCorrectBonusPoints"], 0) or 0),
            "win_condition_mode": str(self._first_present(game, ["code_conspiracy_win_condition_mode", "codeConspiracyWinConditionMode"], "first_to_complete") or "first_to_complete"),
        }

    def update_configuration_without_commit(self, db: DbSession, game_id: str, values: Dict[str, Any]) -> None:
        """Update Code Conspiracy configuration columns without commit."""
        table = self.get_game_table(db)
        updates: Dict[str, Any] = {}

        column_map = {
            "code_length": ["code_conspiracy_code_length", "codeConspiracyCodeLength"],
            "character_set": ["code_conspiracy_character_set", "codeConspiracyCharacterSet"],
            "submission_cooldown_seconds": ["code_conspiracy_submission_cooldown_seconds", "codeConspiracySubmissionCooldownSeconds"],
            "correct_points": ["code_conspiracy_correct_points", "codeConspiracyCorrectPoints"],
            "penalty_enabled": ["code_conspiracy_penalty_enabled", "codeConspiracyPenaltyEnabled"],
            "penalty_value": ["code_conspiracy_penalty_value", "codeConspiracyPenaltyValue"],
            "first_bonus_enabled": ["code_conspiracy_first_correct_bonus_enabled", "codeConspiracyFirstCorrectBonusEnabled"],
            "first_bonus_points": ["code_conspiracy_first_correct_bonus_points", "codeConspiracyFirstCorrectBonusPoints"],
            "win_condition_mode": ["code_conspiracy_win_condition_mode", "codeConspiracyWinConditionMode"],
        }

        for payload_key, candidates in column_map.items():
            if payload_key not in values:
                continue
            for column_name in candidates:
                if column_name in table.c:
                    updates[column_name] = values[payload_key]
                    break

        if updates:
            db.execute(
                update(table)
                .where(table.c["id"] == game_id)
                .values(**updates)
            )

    def end_game_without_commit(self, db: DbSession, game_id: str) -> None:
        """Set game end timestamp and persist current leading team as winner."""
        game_table = self.get_game_table(db)
        teams = self.fetch_teams_by_game_id(db, game_id)
        winner_team_id = None
        if teams:
            teams_sorted = sorted(teams, key=lambda team: int(team.get("geo_score") or 0), reverse=True)
            winner_team_id = str(teams_sorted[0].get("id") or "") or None

        updates: Dict[str, Any] = {
            "end_at": datetime.now(UTC).replace(tzinfo=None),
        }

        winner_columns = ["code_conspiracy_winner_team_id", "codeConspiracyWinnerTeamId"]
        for column_name in winner_columns:
            if column_name in game_table.c:
                updates[column_name] = winner_team_id
                break

        db.execute(
            update(game_table)
            .where(game_table.c["id"] == game_id)
            .values(**updates)
        )
