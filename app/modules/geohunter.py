from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.geohunter_repository import GeoHunterRepository
from app.services.geohunter_service import GeoHunterService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    overview: Dict[str, Any]


class GeoHunterAnswerRequest(BaseModel):
    poi_id: str = Field(min_length=1, max_length=64)
    correct: bool = False


class ActionResponse(BaseModel):
    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class GeoChoiceInput(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    correct: bool = False


class GeoHunterPoiCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    type: str = Field(min_length=1, max_length=24)
    latitude: float
    longitude: float
    radius_meters: int = Field(default=20, ge=1, le=10000)
    content: Optional[str] = None
    question: Optional[str] = None
    expected_answers: list[str] = Field(default_factory=list)
    choices: list[GeoChoiceInput] = Field(default_factory=list)


class GeoHunterPoiUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    type: Optional[str] = Field(default=None, min_length=1, max_length=24)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_meters: Optional[int] = Field(default=None, ge=1, le=10000)
    content: Optional[str] = None
    question: Optional[str] = None
    expected_answers: Optional[list[str]] = None
    choices: Optional[list[GeoChoiceInput]] = None


class GeoHunterRetrySettingsRequest(BaseModel):
    retry_enabled: bool = False
    retry_timeout_seconds: int = Field(default=0, ge=0, le=86400)


class GeoHunterPoiRecordResponse(BaseModel):
    poi: Dict[str, Any]


class GeoHunterPoiListResponse(BaseModel):
    retry_enabled: bool
    retry_timeout_seconds: int
    pois: list[Dict[str, Any]]


class MessageResponse(BaseModel):
    message_key: str


class GeoHunterModule(ApiModule, SharedModuleBase):
    name = "geohunter"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        SharedModuleBase.__init__(self, game_type="geohunter", ws_publisher=ws_publisher)
        self._service = GeoHunterService()
        self._repository = GeoHunterRepository()

    @staticmethod
    def _type_label_key(value: str) -> str:
        mapping = {
            "text": "geohunter.poi.type.text",
            "multiple_choice": "geohunter.poi.type.multiple_choice",
            "open_answer": "geohunter.poi.type.open_answer",
        }
        return mapping.get(value, "geohunter.poi.type.text")

    @staticmethod
    def _normalize_expected_answers(values: Optional[list[str]]) -> Optional[list[str]]:
        if values is None:
            return None
        normalized = [entry.strip() for entry in values if str(entry or "").strip()]
        return normalized or None

    @staticmethod
    def _normalize_choices(values: Optional[list[GeoChoiceInput]]) -> list[Dict[str, Any]]:
        if not values:
            return []
        normalized: list[Dict[str, Any]] = []
        for choice in values:
            label = str(choice.label or "").strip()
            if not label:
                continue
            normalized.append({
                "label": label,
                "is_correct": bool(choice.correct),
            })
        return normalized

    @staticmethod
    def _extract_retry_settings(game: Dict[str, Any]) -> tuple[bool, int]:
        raw_enabled = game.get("geo_hunter_retry_enabled")
        if raw_enabled is None:
            raw_enabled = game.get("geoHunterRetryEnabled")

        raw_timeout = game.get("geo_hunter_retry_timeout_seconds")
        if raw_timeout is None:
            raw_timeout = game.get("geoHunterRetryTimeoutSeconds")

        enabled = bool(raw_enabled)
        timeout_seconds = int(raw_timeout or 0)
        return enabled, timeout_seconds

    def _serialize_poi(self, poi: Dict[str, Any], choices: list[Dict[str, Any]]) -> Dict[str, Any]:
        poi_type = str(poi.get("type") or "text")
        return {
            "id": str(poi.get("id") or ""),
            "game_id": str(poi.get("game_id") or ""),
            "title": str(poi.get("title") or ""),
            "type": poi_type,
            "type_label_key": self._type_label_key(poi_type),
            "latitude": float(poi.get("latitude") or 0),
            "longitude": float(poi.get("longitude") or 0),
            "radius_meters": int(poi.get("radius_meters") or 20),
            "content": poi.get("content"),
            "question": poi.get("question"),
            "expected_answers": list(poi.get("expected_answers") or []),
            "choices": [
                {
                    "id": str(choice.get("id") or ""),
                    "label": str(choice.get("label") or ""),
                    "correct": bool(choice.get("is_correct")),
                }
                for choice in choices
            ],
        }

    def _validate_poi_payload(
        self,
        *,
        poi_type: str,
        latitude: float,
        longitude: float,
        expected_answers: Optional[list[str]],
        choices: list[Dict[str, Any]],
    ) -> None:
        if poi_type not in {"text", "multiple_choice", "open_answer"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.poi.invalidType")

        if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.poi.invalidCoordinates")

        if poi_type == "open_answer" and not expected_answers:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.poi.expectedAnswersRequired")

        if poi_type == "multiple_choice":
            if not choices:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.poi.choicesRequired")
            if not any(bool(choice.get("is_correct")) for choice in choices):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.poi.correctChoiceRequired")

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/geohunter", tags=["geohunter"])

        @router.get(
            "/{game_id}/teams/{team_id}/bootstrap",
            response_model=TeamBootstrapResponse,
            summary=f"{ACCESS_BOTH_LABEL} Team bootstrap",
        )
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            return TeamBootstrapResponse(state=self._service.get_team_bootstrap(db, game_id, team_id))

        @router.get(
            "/{game_id}/overview",
            response_model=AdminOverviewResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Admin overview",
        )
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/pois",
            response_model=GeoHunterPoiListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List POIs",
        )
        def list_pois(game_id: str, principal: CurrentPrincipal, db: DbSession) -> GeoHunterPoiListResponse:
            game = self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            pois = self._repository.fetch_pois_by_game_id(db, game_id)
            choices_by_point = self._repository.fetch_choices_by_poi_ids(db, [str(poi.get("id")) for poi in pois])
            retry_enabled, retry_timeout_seconds = self._extract_retry_settings(game)

            return GeoHunterPoiListResponse(
                retry_enabled=retry_enabled,
                retry_timeout_seconds=retry_timeout_seconds,
                pois=[
                    self._serialize_poi(poi, choices_by_point.get(str(poi.get("id") or ""), []))
                    for poi in pois
                ],
            )

        @router.get(
            "/{game_id}/pois/{poi_id}",
            response_model=GeoHunterPoiRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get POI",
        )
        def get_poi(game_id: str, poi_id: str, principal: CurrentPrincipal, db: DbSession) -> GeoHunterPoiRecordResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            poi = self._repository.get_poi_by_game_id_and_poi_id(db, game_id, poi_id)
            if poi is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="geohunter.poi.notFound")

            choices_by_point = self._repository.fetch_choices_by_poi_ids(db, [poi_id])
            return GeoHunterPoiRecordResponse(
                poi=self._serialize_poi(poi, choices_by_point.get(poi_id, [])),
            )

        @router.post(
            "/{game_id}/pois",
            response_model=GeoHunterPoiRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Create POI",
        )
        def create_poi(
            game_id: str,
            body: GeoHunterPoiCreateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> GeoHunterPoiRecordResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            normalized_expected = self._normalize_expected_answers(body.expected_answers)
            normalized_choices = self._normalize_choices(body.choices)

            self._validate_poi_payload(
                poi_type=body.type,
                latitude=body.latitude,
                longitude=body.longitude,
                expected_answers=normalized_expected,
                choices=normalized_choices,
            )

            poi_id = str(uuid4())
            values: Dict[str, Any] = {
                "id": poi_id,
                "game_id": game_id,
                "title": body.title.strip(),
                "type": body.type,
                "latitude": body.latitude,
                "longitude": body.longitude,
                "radius_meters": int(body.radius_meters),
                "content": body.content.strip() if body.content else None,
                "question": body.question.strip() if body.question else None,
                "expected_answers": normalized_expected,
            }

            if body.type == "text":
                values["question"] = None
                values["expected_answers"] = None
            elif body.type == "multiple_choice":
                values["expected_answers"] = None

            try:
                self._repository.create_poi_without_commit(db, values)
                self._repository.create_choices_without_commit(
                    db,
                    [
                        {
                            "id": str(uuid4()),
                            "point_id": poi_id,
                            "label": choice["label"],
                            "is_correct": bool(choice["is_correct"]),
                        }
                        for choice in normalized_choices
                    ],
                )
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.poi.createFailed") from error

            created = self._repository.get_poi_by_game_id_and_poi_id(db, game_id, poi_id)
            if created is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="geohunter.poi.notFound")

            choices_by_point = self._repository.fetch_choices_by_poi_ids(db, [poi_id])
            return GeoHunterPoiRecordResponse(
                poi=self._serialize_poi(created, choices_by_point.get(poi_id, [])),
            )

        @router.put(
            "/{game_id}/pois/{poi_id}",
            response_model=GeoHunterPoiRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update POI",
        )
        def update_poi(
            game_id: str,
            poi_id: str,
            body: GeoHunterPoiUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> GeoHunterPoiRecordResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_poi_by_game_id_and_poi_id(db, game_id, poi_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="geohunter.poi.notFound")

            current_choices = self._repository.fetch_choices_by_poi_ids(db, [poi_id]).get(poi_id, [])
            effective_type = body.type if body.type is not None else str(current.get("type") or "text")
            effective_lat = float(body.latitude if body.latitude is not None else current.get("latitude") or 0)
            effective_lon = float(body.longitude if body.longitude is not None else current.get("longitude") or 0)

            if body.expected_answers is None:
                effective_expected = list(current.get("expected_answers") or [])
            else:
                effective_expected = self._normalize_expected_answers(body.expected_answers)

            if body.choices is None:
                effective_choices = [
                    {
                        "label": str(choice.get("label") or ""),
                        "is_correct": bool(choice.get("is_correct")),
                    }
                    for choice in current_choices
                ]
            else:
                effective_choices = self._normalize_choices(body.choices)

            self._validate_poi_payload(
                poi_type=effective_type,
                latitude=effective_lat,
                longitude=effective_lon,
                expected_answers=effective_expected,
                choices=effective_choices,
            )

            values: Dict[str, Any] = {}
            if body.title is not None:
                values["title"] = body.title.strip()
            if body.type is not None:
                values["type"] = body.type
            if body.latitude is not None:
                values["latitude"] = body.latitude
            if body.longitude is not None:
                values["longitude"] = body.longitude
            if body.radius_meters is not None:
                values["radius_meters"] = int(body.radius_meters)
            if body.content is not None:
                values["content"] = body.content.strip() or None
            if body.question is not None:
                values["question"] = body.question.strip() or None
            if body.expected_answers is not None:
                values["expected_answers"] = self._normalize_expected_answers(body.expected_answers)

            if effective_type == "text":
                values["question"] = None
                values["expected_answers"] = None
            elif effective_type == "multiple_choice":
                values["expected_answers"] = None

            try:
                self._repository.update_poi_without_commit(db, game_id, poi_id, values)
                if body.choices is not None or body.type is not None:
                    self._repository.delete_choices_by_poi_without_commit(db, poi_id)
                    self._repository.create_choices_without_commit(
                        db,
                        [
                            {
                                "id": str(uuid4()),
                                "point_id": poi_id,
                                "label": choice["label"],
                                "is_correct": bool(choice["is_correct"]),
                            }
                            for choice in effective_choices
                            if effective_type == "multiple_choice"
                        ],
                    )
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.poi.updateFailed") from error

            updated = self._repository.get_poi_by_game_id_and_poi_id(db, game_id, poi_id)
            if updated is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="geohunter.poi.notFound")

            choices_by_point = self._repository.fetch_choices_by_poi_ids(db, [poi_id])
            return GeoHunterPoiRecordResponse(
                poi=self._serialize_poi(updated, choices_by_point.get(poi_id, [])),
            )

        @router.delete(
            "/{game_id}/pois/{poi_id}",
            response_model=MessageResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Delete POI",
        )
        def delete_poi(game_id: str, poi_id: str, principal: CurrentPrincipal, db: DbSession) -> MessageResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_poi_by_game_id_and_poi_id(db, game_id, poi_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="geohunter.poi.notFound")

            try:
                self._repository.delete_poi_without_commit(db, game_id, poi_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.poi.deleteFailed") from error

            return MessageResponse(message_key="geohunter.poi.deleted")

        @router.put(
            "/{game_id}/retry-settings",
            response_model=GeoHunterPoiListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update retry settings",
        )
        def update_retry_settings(
            game_id: str,
            body: GeoHunterRetrySettingsRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> GeoHunterPoiListResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            timeout_seconds = int(body.retry_timeout_seconds)
            if not body.retry_enabled:
                timeout_seconds = 0

            try:
                self._repository.update_retry_settings_without_commit(
                    db,
                    game_id,
                    retry_enabled=body.retry_enabled,
                    retry_timeout_seconds=timeout_seconds,
                )
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.settings.updateFailed") from error

            game = self._require_game(db, game_id)
            pois = self._repository.fetch_pois_by_game_id(db, game_id)
            choices_by_point = self._repository.fetch_choices_by_poi_ids(db, [str(poi.get("id")) for poi in pois])
            retry_enabled, retry_timeout_seconds = self._extract_retry_settings(game)

            return GeoHunterPoiListResponse(
                retry_enabled=retry_enabled,
                retry_timeout_seconds=retry_timeout_seconds,
                pois=[
                    self._serialize_poi(poi, choices_by_point.get(str(poi.get("id") or ""), []))
                    for poi in pois
                ],
            )

        @router.post(
            "/{game_id}/teams/{team_id}/question/answer",
            response_model=ActionResponse,
            summary=f"{ACCESS_BOTH_LABEL} Submit answer",
        )
        def answer_question(
            game_id: str,
            team_id: str,
            body: GeoHunterAnswerRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.poi_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="geohunter.validation.missingPoiId")

            result = self._service.answer_question(
                db,
                game_id=game_id,
                team_id=team_id,
                poi_id=body.poi_id.strip(),
                correct=body.correct,
            )
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        return router
