import os
import re
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.crazy88_repository import Crazy88Repository
from app.services.crazy88_service import Crazy88Service
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    overview: Dict[str, Any]


class SubmitTaskRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    team_message: Optional[str] = Field(default=None, max_length=5000)
    proof_text: Optional[str] = Field(default=None, max_length=5000)


class JudgeSubmissionRequest(BaseModel):
    team_id: str = Field(min_length=1, max_length=64)
    submission_id: str = Field(min_length=1, max_length=64)
    accepted: bool = False
    judge_message: Optional[str] = Field(default=None, max_length=5000)


class ActionResponse(BaseModel):
    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class Crazy88ConfigResponse(BaseModel):
    config: Dict[str, Any]


class Crazy88ConfigUpdateRequest(BaseModel):
    visibility_mode: str = Field(default="all_visible", min_length=3, max_length=24)


class Crazy88TaskPayload(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    description: Optional[str] = None
    points: int = Field(default=1, ge=1, le=100000)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_meters: int = Field(default=25, ge=5, le=5000)
    sort_order: int = Field(default=0, ge=0, le=100000)


class Crazy88TaskResponse(BaseModel):
    task: Dict[str, Any]


class Crazy88TaskListResponse(BaseModel):
    tasks: list[Dict[str, Any]]


class Crazy88ReviewsResponse(BaseModel):
    pending_count: int
    has_assigned_submission: bool
    threads: list[Dict[str, Any]]


class Crazy88ExportFilesRequest(BaseModel):
    grouping: str = Field(default="team_task", min_length=4, max_length=24)


class Crazy88Module(ApiModule, SharedModuleBase):
    name = "crazy88"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        SharedModuleBase.__init__(
            self,
            game_type="crazy_88",
            ws_publisher=ws_publisher,
            game_type_detail_key="crazy88",
        )
        self._service = Crazy88Service()
        self._repository = Crazy88Repository()

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/crazy88", tags=["crazy88"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            state = self._service.get_team_bootstrap(db, game_id, team_id)
            state["config"] = self._repository.get_configuration(db, game_id)
            state["tasks"] = [self._serialize_task(task) for task in self._repository.fetch_tasks_by_game_id(db, game_id)]
            return TeamBootstrapResponse(state=state)

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/config",
            response_model=Crazy88ConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get crazy88 config",
        )
        def get_config(game_id: str, principal: CurrentPrincipal, db: DbSession) -> Crazy88ConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return Crazy88ConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.put(
            "/{game_id}/config",
            response_model=Crazy88ConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update crazy88 config",
        )
        def update_config(
            game_id: str,
            body: Crazy88ConfigUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> Crazy88ConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            visibility_mode = str(body.visibility_mode or "").strip().lower()
            if visibility_mode not in {"all_visible", "geo_locked"}:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.config.invalidVisibilityMode")

            try:
                self._repository.update_configuration_without_commit(db, game_id, {"visibility_mode": visibility_mode})
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.config.updateFailed") from error

            return Crazy88ConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.get(
            "/{game_id}/tasks",
            response_model=Crazy88TaskListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List crazy88 tasks",
        )
        def list_tasks(game_id: str, principal: CurrentPrincipal, db: DbSession) -> Crazy88TaskListResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            tasks = [self._serialize_task(task) for task in self._repository.fetch_tasks_by_game_id(db, game_id)]
            return Crazy88TaskListResponse(tasks=tasks)

        @router.get(
            "/{game_id}/reviews",
            response_model=Crazy88ReviewsResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get crazy88 review queue",
        )
        def get_reviews(game_id: str, principal: CurrentPrincipal, db: DbSession) -> Crazy88ReviewsResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            if principal.principal_type != "user":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auth.user.manageGameRequired")

            try:
                assigned = self._repository.acquire_pending_submission_for_judge(db, game_id, principal.principal_id)
                pending_count = self._repository.count_pending_submissions_by_game_id(db, game_id)
                threads = []
                if assigned:
                    threads = [
                        {
                            "task_id": str(assigned.get("task_id") or ""),
                            "task_title": str(assigned.get("task_title") or ""),
                            "task_points": int(assigned.get("task_points") or 0),
                            "team_id": str(assigned.get("team_id") or ""),
                            "team_name": str(assigned.get("team_name") or ""),
                            "submissions": [self._serialize_submission(record) for record in self._repository.fetch_thread_for_task_and_team(
                                db,
                                game_id,
                                str(assigned.get("task_id") or ""),
                                str(assigned.get("team_id") or ""),
                            )],
                        }
                    ]

                return Crazy88ReviewsResponse(
                    pending_count=pending_count,
                    has_assigned_submission=assigned is not None,
                    threads=threads,
                )
            except KeyError:
                return Crazy88ReviewsResponse(
                    pending_count=0,
                    has_assigned_submission=False,
                    threads=[],
                )

        @router.post(
            "/{game_id}/exports/files",
            summary=f"{ACCESS_ADMIN_LABEL} Export crazy88 proof files",
        )
        def export_files(
            game_id: str,
            body: Crazy88ExportFilesRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> FileResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            grouping = str(body.grouping or "team_task").strip().lower()
            if grouping not in {"team_task", "task_team"}:
                grouping = "team_task"

            records = self._repository.fetch_submissions_for_export(db, game_id)
            files: list[tuple[Path, str, str, str, int]] = []
            for record in records:
                proof_path = str(record.get("proof_path") or "").strip()
                if not proof_path:
                    continue

                absolute_path = self._resolve_proof_path(proof_path)
                if absolute_path is None or not absolute_path.is_file():
                    continue

                team_name = self._sanitize_zip_segment(str(record.get("team_name") or "team"))
                task_title = self._sanitize_zip_segment(str(record.get("task_title") or "task"))
                original_name = str(record.get("proof_original_name") or absolute_path.name)
                safe_original_name = self._sanitize_zip_filename(original_name)
                submitted_stamp = self._submission_timestamp(record.get("submitted_at"))
                submission_id = self._sanitize_zip_segment(str(record.get("id") or "submission"))
                zip_name = f"{submitted_stamp}_{submission_id}_{safe_original_name}"

                zip_relative_path = (
                    f"{task_title}/{team_name}/{zip_name}"
                    if grouping == "task_team"
                    else f"{team_name}/{task_title}/{zip_name}"
                )

                submitted_order = self._submission_order_value(record.get("submitted_at"))
                files.append((absolute_path, zip_relative_path, team_name, task_title, submitted_order))

            if not files:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.export.empty")

            files.sort(key=lambda item: (item[3].lower(), item[2].lower(), item[4]) if grouping == "task_team" else (item[2].lower(), item[3].lower(), item[4]))

            with tempfile.NamedTemporaryFile(prefix="crazy88_export_", suffix=".zip", delete=False) as temp_file:
                zip_path = Path(temp_file.name)

            with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for absolute_path, zip_relative_path, _, _, _ in files:
                    archive.write(absolute_path, arcname=zip_relative_path)

            group_label = "task-team" if grouping == "task_team" else "team-task"
            download_name = f"crazy88-{game_id[:8]}-proof-files-{group_label}.zip"

            return FileResponse(
                path=str(zip_path),
                media_type="application/zip",
                filename=download_name,
                background=BackgroundTask(self._remove_file_safely, str(zip_path)),
            )

        @router.post(
            "/{game_id}/tasks",
            response_model=Crazy88TaskResponse,
            status_code=status.HTTP_201_CREATED,
            summary=f"{ACCESS_ADMIN_LABEL} Create crazy88 task",
        )
        def create_task(game_id: str, body: Crazy88TaskPayload, principal: CurrentPrincipal, db: DbSession) -> Crazy88TaskResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            values = self._validate_task_payload(body.model_dump(), self._repository.get_configuration(db, game_id))
            values["game_id"] = game_id

            try:
                task_id = self._repository.create_task_without_commit(db, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.task.createFailed") from error

            task = self._repository.get_task_by_game_id_and_task_id(db, game_id, task_id)
            if task is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="crazy88.task.fetchFailed")
            return Crazy88TaskResponse(task=self._serialize_task(task))

        @router.put(
            "/{game_id}/tasks/{task_id}",
            response_model=Crazy88TaskResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update crazy88 task",
        )
        def update_task(
            game_id: str,
            task_id: str,
            body: Crazy88TaskPayload,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> Crazy88TaskResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_task_by_game_id_and_task_id(db, game_id, task_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="crazy88.task.notFound")

            values = self._validate_task_payload(body.model_dump(), self._repository.get_configuration(db, game_id))

            try:
                self._repository.update_task_without_commit(db, game_id, task_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.task.updateFailed") from error

            task = self._repository.get_task_by_game_id_and_task_id(db, game_id, task_id)
            if task is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="crazy88.task.fetchFailed")
            return Crazy88TaskResponse(task=self._serialize_task(task))

        @router.delete(
            "/{game_id}/tasks/{task_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            summary=f"{ACCESS_ADMIN_LABEL} Delete crazy88 task",
        )
        def delete_task(game_id: str, task_id: str, principal: CurrentPrincipal, db: DbSession) -> None:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_task_by_game_id_and_task_id(db, game_id, task_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="crazy88.task.notFound")

            try:
                self._repository.delete_task_without_commit(db, game_id, task_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.task.deleteFailed") from error

        @router.post("/{game_id}/teams/{team_id}/task/submit", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Submit task")
        def submit_task(game_id: str, team_id: str, body: SubmitTaskRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            task_id = body.task_id.strip()
            if not task_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.validation.missingTaskId")

            task = self._repository.get_task_by_game_id_and_task_id(db, game_id, task_id)
            if task is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="crazy88.task.notFound")

            pending = self._repository.find_pending_submission_for_task_and_team(db, task_id, team_id)
            if pending is not None:
                return ActionResponse(
                    success=True,
                    message_key=self._localize_message_key("crazy88.task.pendingExists", locale),
                    action_id=str(pending.get("id") or ""),
                    points_awarded=0,
                    state_version=0,
                )

            submission_id = str(uuid4())
            values: Dict[str, Any] = {
                "id": submission_id,
                "status": "pending",
            }

            submission_table = self._repository.get_submission_table(db)
            task_column = self._repository._pick_column(submission_table, ["task_id", "taskId"])
            team_column = self._repository._pick_column(submission_table, ["team_id", "teamId"])
            submitted_column = self._repository._pick_column(submission_table, ["submitted_at", "submittedAt"])
            team_message_column = self._repository._pick_column(submission_table, ["team_message", "teamMessage"])
            proof_text_column = self._repository._pick_column(submission_table, ["proof_text", "proofText"])

            if task_column is None or team_column is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="crazy88.submission.schemaMissing")

            values[task_column] = task_id
            values[team_column] = team_id
            if submitted_column:
                values[submitted_column] = datetime.now(UTC).replace(tzinfo=None)
            if team_message_column:
                values[team_message_column] = str(body.team_message or "").strip() or None
            if proof_text_column:
                values[proof_text_column] = str(body.proof_text or "").strip() or None

            try:
                created_id = self._repository.create_submission_without_commit(db, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.task.submitFailed") from error

            return ActionResponse(
                success=True,
                message_key=self._localize_message_key("crazy88.task.submitted", locale),
                action_id=created_id,
                points_awarded=0,
                state_version=0,
            )

        @router.post("/{game_id}/review/judge", response_model=ActionResponse, summary=f"{ACCESS_ADMIN_LABEL} Judge submission")
        def judge_submission(game_id: str, body: JudgeSubmissionRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            if not body.submission_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.validation.missingSubmissionId")

            submission_id = body.submission_id.strip()
            submission = self._repository.fetch_submission_by_id_for_game(db, game_id, submission_id)
            if submission is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="crazy88.submission.notFound")

            team_id = body.team_id.strip()
            if team_id and str(submission.get("team_id") or "") != team_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.submission.invalidTeam")

            current_status = str(submission.get("status") or "").lower()
            if current_status != "pending":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.submission.notPending")

            reviewed_by_id = str(submission.get("reviewed_by_id") or "").strip()
            if reviewed_by_id and reviewed_by_id != principal.principal_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.review.notAssigned")

            status_value = "accepted" if body.accepted else "rejected"
            points_awarded = int(submission.get("task_points") or 0) if body.accepted else 0
            values: Dict[str, Any] = {}

            submission_table = self._repository.get_submission_table(db)
            status_column = self._repository._pick_column(submission_table, ["status"])
            reviewed_at_column = self._repository._pick_column(submission_table, ["reviewed_at", "reviewedAt"])
            reviewed_by_column = self._repository._pick_column(submission_table, ["reviewed_by_id", "reviewedBy_id", "reviewedById"])
            judge_message_column = self._repository._pick_column(submission_table, ["judge_message", "judgeMessage"])

            if status_column is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="crazy88.submission.schemaMissing")

            values[status_column] = status_value
            if reviewed_at_column:
                values[reviewed_at_column] = datetime.now(UTC).replace(tzinfo=None)
            if reviewed_by_column:
                values[reviewed_by_column] = principal.principal_id
            if judge_message_column:
                values[judge_message_column] = str(body.judge_message or "").strip() or None

            try:
                self._repository.update_submission_without_commit(db, submission_id, values)
                if points_awarded > 0:
                    self._repository.increment_team_geo_score_without_commit(db, str(submission.get("team_id") or ""), points_awarded)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.review.updateFailed") from error

            return ActionResponse(
                success=True,
                message_key=self._localize_message_key("crazy88.review.judged", locale),
                action_id=submission_id,
                points_awarded=points_awarded,
                state_version=0,
            )

        return router

    @staticmethod
    def _serialize_task(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(record.get("id") or ""),
            "title": str(record.get("title") or ""),
            "description": record.get("description"),
            "points": int(record.get("points") or 1),
            "latitude": None if record.get("latitude") is None else float(record.get("latitude")),
            "longitude": None if record.get("longitude") is None else float(record.get("longitude")),
            "radius_meters": int(record.get("radius_meters") or 25),
            "sort_order": int(record.get("sort_order") or 0),
        }

    @staticmethod
    def _serialize_submission(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(record.get("id") or ""),
            "task_id": str(record.get("task_id") or ""),
            "team_id": str(record.get("team_id") or ""),
            "status": str(record.get("status") or "pending"),
            "submitted_at": Crazy88Module._to_iso(record.get("submitted_at")),
            "reviewed_at": Crazy88Module._to_iso(record.get("reviewed_at")),
            "team_message": record.get("team_message"),
            "judge_message": record.get("judge_message"),
            "proof_path": record.get("proof_path"),
            "proof_original_name": record.get("proof_original_name"),
            "proof_mime_type": record.get("proof_mime_type"),
            "proof_size": None if record.get("proof_size") is None else int(record.get("proof_size") or 0),
            "proof_text": record.get("proof_text"),
        }

    @staticmethod
    def _to_iso(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        text = str(value).strip()
        return text or None

    @staticmethod
    def _sanitize_zip_segment(value: str) -> str:
        cleaned = re.sub(r"[^\w\- ]+", "_", value.strip(), flags=re.UNICODE)
        cleaned = re.sub(r"\s+", " ", cleaned, flags=re.UNICODE).strip()
        return cleaned or "unknown"

    @staticmethod
    def _sanitize_zip_filename(value: str) -> str:
        cleaned = re.sub(r"[^\w\-_. ]+", "_", value.strip(), flags=re.UNICODE)
        cleaned = re.sub(r"\s+", " ", cleaned, flags=re.UNICODE).strip(" .")
        return cleaned or "proof.bin"

    @staticmethod
    def _submission_timestamp(value: Any) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y%m%d_%H%M%S")
        text = str(value or "").strip()
        if not text:
            return "unknown_time"
        normalized = re.sub(r"[^0-9]", "", text)
        if len(normalized) >= 14:
            return f"{normalized[:8]}_{normalized[8:14]}"
        return "unknown_time"

    @staticmethod
    def _submission_order_value(value: Any) -> int:
        if isinstance(value, datetime):
            return int(value.timestamp())
        text = str(value or "").strip()
        if not text:
            return 0
        normalized = re.sub(r"[^0-9]", "", text)
        if not normalized:
            return 0
        try:
            return int(normalized[:14])
        except ValueError:
            return 0

    @staticmethod
    def _workspace_root() -> Path:
        return Path(__file__).resolve().parents[3]

    @classmethod
    def _resolve_proof_path(cls, proof_path: str) -> Optional[Path]:
        relative = proof_path.strip().lstrip("/")
        if not relative:
            return None

        roots = [
            cls._workspace_root(),
            cls._workspace_root() / "backend",
            cls._workspace_root() / "jotigames-old",
        ]
        for root in roots:
            candidate = root / "public" / relative
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _remove_file_safely(path: str) -> None:
        try:
            if path and os.path.exists(path):
                os.unlink(path)
        except OSError:
            return

    @staticmethod
    def _validate_task_payload(payload: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.task.invalidTitle")

        visibility_mode = str(config.get("visibility_mode") or "all_visible")
        latitude = payload.get("latitude")
        longitude = payload.get("longitude")
        if visibility_mode == "geo_locked":
            if (latitude is None) != (longitude is None):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="crazy88.task.invalidLocation")

        if visibility_mode != "geo_locked":
            latitude = None
            longitude = None

        return {
            "title": title,
            "description": str(payload.get("description") or "").strip() or None,
            "points": max(1, int(payload.get("points") or 1)),
            "latitude": None if latitude is None else float(latitude),
            "longitude": None if longitude is None else float(longitude),
            "radius_meters": max(5, int(payload.get("radius_meters") or 25)),
            "sort_order": max(0, int(payload.get("sort_order") or 0)),
        }
