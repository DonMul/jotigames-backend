from typing import Any, Dict, Optional

from sqlalchemy import Table, delete, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class Crazy88Repository(GameLogicStateRepository):
    @staticmethod
    def _first_present(row: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
        for key in keys:
            if key in row:
                return row.get(key)
        return default

    def get_task_table(self, db: DbSession) -> Table:
        return self._get_table(db, "crazy88_task")

    def get_submission_table(self, db: DbSession) -> Table:
        return self._get_table(db, "crazy88_submission")

    @staticmethod
    def _pick_column(table: Table, candidates: list[str]) -> Optional[str]:
        for candidate in candidates:
            if candidate in table.c:
                return candidate
        return None

    def get_configuration(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        game = self.get_game_by_id(db, game_id)
        if game is None:
            return {}

        return {
            "visibility_mode": str(self._first_present(game, ["crazy88_visibility_mode", "crazy88VisibilityMode"], "all_visible") or "all_visible"),
        }

    def update_configuration_without_commit(self, db: DbSession, game_id: str, values: Dict[str, Any]) -> None:
        table = self.get_game_table(db)
        updates: Dict[str, Any] = {}

        column_map = {
            "visibility_mode": ["crazy88_visibility_mode", "crazy88VisibilityMode"],
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

    def fetch_tasks_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_task_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def _submission_context_select(self, db: DbSession):
        submission_table = self.get_submission_table(db)
        task_table = self.get_task_table(db)
        team_table = self.get_team_table(db)

        submission_task_id = self._pick_column(submission_table, ["task_id", "taskId"])
        submission_team_id = self._pick_column(submission_table, ["team_id", "teamId"])
        status_column = self._pick_column(submission_table, ["status"])
        submitted_at_column = self._pick_column(submission_table, ["submitted_at", "submittedAt"])
        reviewed_at_column = self._pick_column(submission_table, ["reviewed_at", "reviewedAt"])
        reviewed_by_column = self._pick_column(submission_table, ["reviewed_by_id", "reviewedBy_id", "reviewedById"])
        team_message_column = self._pick_column(submission_table, ["team_message", "teamMessage"])
        judge_message_column = self._pick_column(submission_table, ["judge_message", "judgeMessage"])
        proof_path_column = self._pick_column(submission_table, ["proof_path", "proofPath"])
        proof_original_name_column = self._pick_column(submission_table, ["proof_original_name", "proofOriginalName"])
        proof_mime_type_column = self._pick_column(submission_table, ["proof_mime_type", "proofMimeType"])
        proof_size_column = self._pick_column(submission_table, ["proof_size", "proofSize"])
        proof_text_column = self._pick_column(submission_table, ["proof_text", "proofText"])

        if submission_task_id is None or submission_team_id is None or status_column is None:
            raise KeyError("crazy88_submission schema is missing required columns")

        if "game_id" in task_table.c:
            task_game_id_column = "game_id"
        elif "gameId" in task_table.c:
            task_game_id_column = "gameId"
        else:
            raise KeyError("crazy88_task schema is missing game_id")

        selected_columns = [
            submission_table.c["id"].label("id"),
            submission_table.c[submission_task_id].label("task_id"),
            submission_table.c[submission_team_id].label("team_id"),
            submission_table.c[status_column].label("status"),
            task_table.c["title"].label("task_title"),
            task_table.c["points"].label("task_points"),
            team_table.c["name"].label("team_name"),
        ]
        if submitted_at_column:
            selected_columns.append(submission_table.c[submitted_at_column].label("submitted_at"))
        if reviewed_at_column:
            selected_columns.append(submission_table.c[reviewed_at_column].label("reviewed_at"))
        if reviewed_by_column:
            selected_columns.append(submission_table.c[reviewed_by_column].label("reviewed_by_id"))
        if team_message_column:
            selected_columns.append(submission_table.c[team_message_column].label("team_message"))
        if judge_message_column:
            selected_columns.append(submission_table.c[judge_message_column].label("judge_message"))
        if proof_path_column:
            selected_columns.append(submission_table.c[proof_path_column].label("proof_path"))
        if proof_original_name_column:
            selected_columns.append(submission_table.c[proof_original_name_column].label("proof_original_name"))
        if proof_mime_type_column:
            selected_columns.append(submission_table.c[proof_mime_type_column].label("proof_mime_type"))
        if proof_size_column:
            selected_columns.append(submission_table.c[proof_size_column].label("proof_size"))
        if proof_text_column:
            selected_columns.append(submission_table.c[proof_text_column].label("proof_text"))

        query = (
            select(*selected_columns)
            .select_from(
                submission_table.join(task_table, submission_table.c[submission_task_id] == task_table.c["id"]).join(
                    team_table,
                    submission_table.c[submission_team_id] == team_table.c["id"],
                )
            )
            .where(task_table.c[task_game_id_column] == game_id)
        )

        return {
            "submission_table": submission_table,
            "submission_task_id": submission_task_id,
            "submission_team_id": submission_team_id,
            "status_column": status_column,
            "submitted_at_column": submitted_at_column,
            "reviewed_at_column": reviewed_at_column,
            "reviewed_by_column": reviewed_by_column,
            "team_message_column": team_message_column,
            "judge_message_column": judge_message_column,
            "proof_path_column": proof_path_column,
            "proof_original_name_column": proof_original_name_column,
            "proof_mime_type_column": proof_mime_type_column,
            "proof_size_column": proof_size_column,
            "proof_text_column": proof_text_column,
            "query": query,
        }

    def fetch_submission_threads_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        context = self._submission_context_select(db, game_id)
        submission_table = context["submission_table"]
        submitted_at_column = context["submitted_at_column"]
        order_column = submission_table.c[submitted_at_column] if submitted_at_column else submission_table.c["id"]
        rows = db.execute(
            context["query"].order_by(
                context["query"].selected_columns.task_title,
                context["query"].selected_columns.team_name,
                order_column,
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    def count_pending_submissions_by_game_id(self, db: DbSession, game_id: str) -> int:
        records = self.fetch_submission_threads_by_game_id(db, game_id)
        return sum(1 for record in records if str(record.get("status") or "").lower() == "pending")

    def fetch_submission_by_id_for_game(self, db: DbSession, game_id: str, submission_id: str) -> Dict[str, Any] | None:
        context = self._submission_context_select(db, game_id)
        row = db.execute(
            context["query"].where(context["submission_table"].c["id"] == submission_id).limit(1)
        ).mappings().first()
        return dict(row) if row else None

    def fetch_thread_for_task_and_team(self, db: DbSession, game_id: str, task_id: str, team_id: str) -> list[Dict[str, Any]]:
        context = self._submission_context_select(db, game_id)
        submission_table = context["submission_table"]
        submitted_at_column = context["submitted_at_column"]
        order_column = submission_table.c[submitted_at_column] if submitted_at_column else submission_table.c["id"]
        rows = db.execute(
            context["query"]
            .where(submission_table.c[context["submission_task_id"]] == task_id)
            .where(submission_table.c[context["submission_team_id"]] == team_id)
            .order_by(order_column)
        ).mappings().all()
        return [dict(row) for row in rows]

    def find_pending_submission_for_task_and_team(self, db: DbSession, task_id: str, team_id: str) -> Dict[str, Any] | None:
        table = self.get_submission_table(db)
        task_column = self._pick_column(table, ["task_id", "taskId"])
        team_column = self._pick_column(table, ["team_id", "teamId"])
        status_column = self._pick_column(table, ["status"])
        submitted_at_column = self._pick_column(table, ["submitted_at", "submittedAt"]) or "id"

        if task_column is None or team_column is None or status_column is None:
            return None

        row = db.execute(
            select(table)
            .where(table.c[task_column] == task_id)
            .where(table.c[team_column] == team_id)
            .where(table.c[status_column] == "pending")
            .order_by(table.c[submitted_at_column].desc())
            .limit(1)
        ).mappings().first()
        return dict(row) if row else None

    def create_submission_without_commit(self, db: DbSession, values: Dict[str, Any]) -> str:
        table = self.get_submission_table(db)
        if "id" in values and values["id"]:
            db.execute(insert(table).values(**values))
            return str(values["id"])

        result = db.execute(insert(table).values(**values).returning(table.c["id"]))
        return str(result.scalar_one())

    def update_submission_without_commit(self, db: DbSession, submission_id: str, values: Dict[str, Any]) -> None:
        table = self.get_submission_table(db)
        db.execute(update(table).where(table.c["id"] == submission_id).values(**values))

    def acquire_pending_submission_for_judge(self, db: DbSession, game_id: str, judge_id: str) -> Dict[str, Any] | None:
        context = self._submission_context_select(db, game_id)
        status_column = context["status_column"]
        reviewed_by_column = context["reviewed_by_column"]
        submitted_at_column = context["submitted_at_column"]
        submission_table = context["submission_table"]

        if reviewed_by_column:
            assigned = db.execute(
                context["query"]
                .where(submission_table.c[status_column] == "pending")
                .where(submission_table.c[reviewed_by_column] == judge_id)
                .order_by(submission_table.c[submitted_at_column] if submitted_at_column else submission_table.c["id"])
                .limit(1)
            ).mappings().first()
            if assigned:
                return dict(assigned)

        for _ in range(3):
            pending = db.execute(
                context["query"]
                .where(submission_table.c[status_column] == "pending")
                .where(submission_table.c[reviewed_by_column].is_(None) if reviewed_by_column else True)
                .order_by(submission_table.c[submitted_at_column] if submitted_at_column else submission_table.c["id"])
                .limit(1)
            ).mappings().first()

            if not pending:
                return None

            if not reviewed_by_column:
                return dict(pending)

            updated = db.execute(
                update(submission_table)
                .where(submission_table.c["id"] == str(pending.get("id")))
                .where(submission_table.c[status_column] == "pending")
                .where(submission_table.c[reviewed_by_column].is_(None))
                .values(**{reviewed_by_column: judge_id})
            )
            if updated.rowcount == 1:
                refreshed = db.execute(
                    context["query"].where(submission_table.c["id"] == str(pending.get("id"))).limit(1)
                ).mappings().first()
                if refreshed:
                    return dict(refreshed)

        if not reviewed_by_column:
            return None

        row = db.execute(
            context["query"]
            .where(submission_table.c[status_column] == "pending")
            .where(submission_table.c[reviewed_by_column] == judge_id)
            .order_by(submission_table.c[submitted_at_column] if submitted_at_column else submission_table.c["id"])
            .limit(1)
        ).mappings().first()
        return dict(row) if row else None

    def fetch_submissions_for_export(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        records = self.fetch_submission_threads_by_game_id(db, game_id)
        return [record for record in records if record.get("proof_path")]

    def get_task_by_game_id_and_task_id(self, db: DbSession, game_id: str, task_id: str) -> Dict[str, Any] | None:
        table = self.get_task_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == task_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def create_task_without_commit(self, db: DbSession, values: Dict[str, Any]) -> str:
        table = self.get_task_table(db)
        result = db.execute(insert(table).values(**values).returning(table.c["id"]))
        return str(result.scalar_one())

    def update_task_without_commit(self, db: DbSession, game_id: str, task_id: str, values: Dict[str, Any]) -> None:
        table = self.get_task_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == task_id)
            .values(**values)
        )

    def delete_task_without_commit(self, db: DbSession, game_id: str, task_id: str) -> None:
        table = self.get_task_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == task_id)
        )
