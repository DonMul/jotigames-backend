import io
import json
from datetime import UTC, datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import get_settings
from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL
from app.repositories.exploding_kittens_repository import ExplodingKittensRepository
from app.repositories.game_repository import GameRepository
from app.services.i18n import translate_value
from app.services.exploding_kittens_service import ExplodingKittensResult, ExplodingKittensService
from app.services.ws_client import WsEventPublisher


class MessageKeyResponse(BaseModel):
    message_key: str


class ExplodingKittensCardResponse(BaseModel):
    card: Dict[str, Any]


class ExplodingKittensCardsResponse(BaseModel):
    cards: list[Dict[str, Any]]


class ExplodingKittensStateResponse(BaseModel):
    state: Dict[str, Any]


class ScanResponse(BaseModel):
    success: bool
    status: str
    message_key: str
    card: Optional[Dict[str, Any]] = None
    pending_state: Optional[str] = None


class ResolveStateResponse(BaseModel):
    success: bool
    status: str
    message_key: str
    pending_state: Optional[str] = None


class ResolveActionResponse(BaseModel):
    success: bool
    status: str
    message_key: str
    card_type: Optional[str] = None


class ExplodingKittensLivesResponse(BaseModel):
    team_id: str
    lives: int


class UseComboResponse(BaseModel):
    success: bool
    combo_type: str


class PendingActionsResponse(BaseModel):
    actions: list[Dict[str, Any]]


class PlayCardRequest(BaseModel):
    target_team_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class PlayCardResponse(BaseModel):
    success: bool
    message_key: str
    action_type: str


class RemoveRandomTeamHandCardRequest(BaseModel):
    card_type: str = Field(min_length=1, max_length=32)


class RemoveRandomTeamHandCardResponse(BaseModel):
    team_id: str
    card_type: str
    removed: bool
    card_id: Optional[str] = None


class AddRandomTeamHandCardResponse(BaseModel):
    team_id: str
    card_type: str
    added: bool
    card_id: Optional[str] = None


class AddCardsRequest(BaseModel):
    card_type: str = Field(min_length=1, max_length=32)
    quantity: int = Field(default=1, ge=1, le=200)


class CardCreateRequest(BaseModel):
    card_type: str = Field(min_length=1, max_length=32)
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    image_path: Optional[str] = Field(default=None, min_length=1, max_length=255)


class CardUpdateRequest(BaseModel):
    card_type: Optional[str] = Field(default=None, min_length=1, max_length=32)
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    image_path: Optional[str] = Field(default=None, min_length=1, max_length=255)
    holder_team_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class ScanCardRequest(BaseModel):
    qr_token: str = Field(min_length=1, max_length=64)
    target_team_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class ResolveStateRequest(BaseModel):
    resolve_skip: bool = False
    confirm_peek: bool = False
    reject_peek: bool = False
    qr_token: Optional[str] = Field(default=None, min_length=1, max_length=64)
    target_team_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class ResolveActionRequest(BaseModel):
    use_nope: bool = False


class UseComboRequest(BaseModel):
    card_ids: list[str] = Field(min_length=2)
    target_team_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    requested_card_type: Optional[str] = Field(default=None, min_length=1, max_length=32)


class AdjustLivesRequest(BaseModel):
    delta: int = Field(ge=-100, le=100)


class ExplodingKittensModule(ApiModule):
    name = "exploding-kittens"

    _STATE_FLAG_TO_KEY = {
        "pending_skip": "skip",
        "pending_peek": "see_the_future",
        "pending_attack": "attack",
    }

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        self._ws_publisher = ws_publisher
        self._gameRepository = GameRepository()
        self._repository = ExplodingKittensRepository()
        self._service = ExplodingKittensService()

    def _require_exploding_kittens_game(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        game = self._repository.getGameById(db, game_id)
        if game is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.notFound")
        if str(game.get("game_type") or "") != "exploding_kittens":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.gameTypeRequired")
        return game

    def _require_user_manage_access(self, db: DbSession, game_id: str, principal: CurrentPrincipal) -> None:
        if principal.principal_type != "user":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="game.auth.userRequired")

        is_owner = self._gameRepository.isGameOwnerByGameIdAndUserId(db, game_id, principal.principal_id)
        is_admin = self._gameRepository.hasGameManagerByGameIdAndUserId(db, game_id, principal.principal_id)
        if is_owner or is_admin:
            return

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="game.auth.manageAccessRequired")

    def _require_team_self_or_manage_access(
        self,
        db: DbSession,
        game_id: str,
        team_id: str,
        principal: CurrentPrincipal,
    ) -> None:
        team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
        if team is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team.notFound")

        if principal.principal_type == "team":
            if principal.principal_id != team_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team.auth.selfRequired")
            return

        self._require_user_manage_access(db, game_id, principal)

    def _count_team_hand_cards_by_type(self, db: DbSession, team_id: str, card_type: str) -> int:
        hand_cards = self._repository.fetchHandCardsByTeamId(db, team_id)
        return sum(1 for card in hand_cards if str(card.get("type") or "") == card_type)

    def _publish_hand_adjusted_amount_event(
        self,
        *,
        game_id: str,
        team_id: str,
        card_type: str,
        amount: int,
    ) -> None:
        self._ws_publisher.publish(
            event="admin.exploding_kittens.card.adjust_amount",
            payload={
                "game_id": game_id,
                "team_id": team_id,
                "card_type": card_type,
                "amount": amount,
            },
            channels=[f"channel:{game_id}:admin"],
        )

    def _publish_team_general_message(
        self,
        *,
        game_id: str,
        team_id: str,
        message: str = "",
        message_key: str = "",
        message_params: Optional[Dict[str, Any]] = None,
        level: str = "info",
        from_value: str = "system",
        title: str = "",
        title_key: str = "",
    ) -> None:
        body = str(message or "").strip()
        normalized_message_key = str(message_key or "").strip()
        normalized_team_id = str(team_id or "").strip()
        if (not body and not normalized_message_key) or not normalized_team_id:
            return

        self._ws_publisher.publish(
            event="team.general.message",
            payload={
                "teamId": normalized_team_id,
                "id": str(uuid4()),
                "message": body,
                "message_key": normalized_message_key,
                "messageKey": normalized_message_key,
                "message_params": message_params or {},
                "messageParams": message_params or {},
                "title": str(title or "").strip(),
                "title_key": str(title_key or "").strip(),
                "titleKey": str(title_key or "").strip(),
                "level": str(level or "info").strip() or "info",
                "from": str(from_value or "system").strip() or "system",
                "gameId": game_id,
                "createdAt": datetime.now(UTC).isoformat(),
            },
            channels=[f"channel:{game_id}:{normalized_team_id}"],
        )

    def _publish_team_hand_diff_events(
        self,
        *,
        game_id: str,
        team_id: str,
        before_cards: list[Dict[str, Any]],
        after_cards: list[Dict[str, Any]],
    ) -> None:
        before_by_id = {
            str(card.get("id") or ""): card
            for card in before_cards
            if str(card.get("id") or "").strip()
        }
        after_by_id = {
            str(card.get("id") or ""): card
            for card in after_cards
            if str(card.get("id") or "").strip()
        }

        added_ids = [card_id for card_id in after_by_id if card_id not in before_by_id]
        removed_ids = [card_id for card_id in before_by_id if card_id not in after_by_id]

        for card_id in removed_ids:
            removed_card = before_by_id.get(card_id) or {}
            self._ws_publisher.publish(
                event="team.exploding_kittens.card.remove",
                payload={
                    "game_id": game_id,
                    "team_id": team_id,
                    "id": card_id,
                    "type": str(removed_card.get("type") or ""),
                },
                channels=[f"channel:{game_id}:{team_id}", f"channel:{game_id}:admin"],
            )

        for card_id in added_ids:
            added_card = after_by_id.get(card_id) or {}
            card_type = str(added_card.get("type") or "")
            self._ws_publisher.publish(
                event="team.exploding_kittens.card.add",
                payload={
                    "game_id": game_id,
                    "team_id": team_id,
                    "id": card_id,
                    "name": str(added_card.get("title") or ""),
                    "type": card_type,
                    "image": str(added_card.get("image_path") or ""),
                },
                channels=[f"channel:{game_id}:{team_id}"],
            )

        changed_types = {
            str((after_by_id.get(card_id) or {}).get("type") or "")
            for card_id in added_ids
            if str((after_by_id.get(card_id) or {}).get("type") or "").strip()
        }
        changed_types.update(
            {
                str((before_by_id.get(card_id) or {}).get("type") or "")
                for card_id in removed_ids
                if str((before_by_id.get(card_id) or {}).get("type") or "").strip()
            }
        )
        if not changed_types:
            return

        for card_type in changed_types:
            amount = sum(1 for card in after_by_id.values() if str(card.get("type") or "") == card_type)
            self._publish_hand_adjusted_amount_event(
                game_id=game_id,
                team_id=team_id,
                card_type=card_type,
                amount=amount,
            )

    @staticmethod
    def _action_payload(action: Dict[str, Any]) -> Dict[str, Any]:
        created_at = action.get("created_at")
        created_at_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
        context_value = action.get("context")
        if isinstance(context_value, (dict, list)):
            context_serialized: Any = context_value
        elif context_value is None:
            context_serialized = None
        else:
            context_serialized = str(context_value)

        parsed_context: Dict[str, Any] = {}
        if isinstance(context_serialized, dict):
            parsed_context = context_serialized
        elif isinstance(context_serialized, str) and context_serialized.strip():
            try:
                maybe_context = json.loads(context_serialized)
                if isinstance(maybe_context, dict):
                    parsed_context = maybe_context
            except json.JSONDecodeError:
                parsed_context = {}

        requested_card_type = str(
            parsed_context.get("requestedCardType")
            or parsed_context.get("requested_card_type")
            or ""
        ).strip()

        return {
            "id": str(action.get("id") or ""),
            "game_id": str(action.get("game_id") or ""),
            "source_team_id": str(action.get("source_team_id") or ""),
            "target_team_id": str(action.get("target_team_id") or ""),
            "card_id": str(action.get("card_id") or "") or None,
            "action_type": str(action.get("action_type") or ""),
            "status": str(action.get("status") or "pending"),
            "created_at": created_at_value,
            "context": context_serialized,
            "requested_card_type": requested_card_type or None,
        }

    @staticmethod
    def _parse_action_context(action: Dict[str, Any]) -> Dict[str, Any]:
        raw = action.get("context")
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _describe_action_type(self, action: Dict[str, Any]) -> str:
        action_type = str(action.get("action_type") or "").strip()
        context = self._parse_action_context(action)

        if action_type == "favor":
            return "Favor"
        if action_type == "attack":
            return "Attack"
        if action_type == "combo_two_same":
            return "Combo 2"
        if action_type == "combo_three_same":
            requested_type = str(context.get("requestedCardType") or context.get("requested_card_type") or "").strip()
            if requested_type:
                return f"Combo 3 ({requested_type})"
            return "Combo 3"
        return action_type or "action"

    @staticmethod
    def _summarize_hand_type_delta(
        *,
        before_cards: list[Dict[str, Any]],
        after_cards: list[Dict[str, Any]],
    ) -> tuple[list[str], list[str]]:
        before_by_id = {
            str(card.get("id") or ""): card
            for card in before_cards
            if str(card.get("id") or "").strip()
        }
        after_by_id = {
            str(card.get("id") or ""): card
            for card in after_cards
            if str(card.get("id") or "").strip()
        }

        added_types = [
            str((after_by_id.get(card_id) or {}).get("type") or "").strip()
            for card_id in after_by_id
            if card_id not in before_by_id
        ]
        removed_types = [
            str((before_by_id.get(card_id) or {}).get("type") or "").strip()
            for card_id in before_by_id
            if card_id not in after_by_id
        ]

        added = [card_type for card_type in added_types if card_type]
        removed = [card_type for card_type in removed_types if card_type]
        return added, removed

    @staticmethod
    def _format_card_types(card_types: list[str]) -> str:
        if not card_types:
            return ""
        return ", ".join(card_types)

    def _publish_action_resolution_messages(
        self,
        *,
        game_id: str,
        action: Dict[str, Any],
        use_nope: bool,
        target_team_before: Optional[Dict[str, Any]],
        target_team_after: Optional[Dict[str, Any]],
        source_team_before: Optional[Dict[str, Any]],
        source_team_after: Optional[Dict[str, Any]],
        target_hand_before: list[Dict[str, Any]],
        target_hand_after: list[Dict[str, Any]],
        source_hand_before: list[Dict[str, Any]],
        source_hand_after: list[Dict[str, Any]],
    ) -> None:
        target_team_id = str(action.get("target_team_id") or "").strip()
        source_team_id = str(action.get("source_team_id") or "").strip()

        target_name = str((target_team_after or target_team_before or {}).get("name") or target_team_id or "")
        source_name = str((source_team_after or source_team_before or {}).get("name") or source_team_id or "")

        target_lives_before = self._extract_team_lives(target_team_before) or 0
        target_lives_after = self._extract_team_lives(target_team_after) or target_lives_before
        target_lives_delta = target_lives_after - target_lives_before
        target_added, target_removed = self._summarize_hand_type_delta(
            before_cards=target_hand_before,
            after_cards=target_hand_after,
        )

        source_lives_before = self._extract_team_lives(source_team_before) or 0
        source_lives_after = self._extract_team_lives(source_team_after) or source_lives_before
        source_lives_delta = source_lives_after - source_lives_before
        source_added, source_removed = self._summarize_hand_type_delta(
            before_cards=source_hand_before,
            after_cards=source_hand_after,
        )

        if target_team_id:
            if use_nope:
                target_message_key = "teamDashboard.popup.actionNopedTarget"
                target_params: Dict[str, Any] = {
                    "team": source_name,
                    "event": str(action.get("action_type") or ""),
                }
            else:
                target_message_key = "teamDashboard.popup.actionResolvedTarget"
                target_params = {
                    "team": source_name,
                    "event": str(action.get("action_type") or ""),
                    "livesDelta": target_lives_delta,
                    "cardsLost": target_removed,
                    "cardsGained": target_added,
                }

            self._publish_team_general_message(
                game_id=game_id,
                team_id=target_team_id,
                message="",
                message_key=target_message_key,
                message_params=target_params,
                title_key="teamDashboard.popupTitle",
            )

        if source_team_id and source_team_id != target_team_id:
            if use_nope:
                source_message_key = "teamDashboard.popup.actionNopedSource"
                source_params: Dict[str, Any] = {
                    "team": target_name,
                    "event": str(action.get("action_type") or ""),
                }
            else:
                source_message_key = "teamDashboard.popup.actionResolvedSource"
                source_params = {
                    "team": target_name,
                    "event": str(action.get("action_type") or ""),
                    "targetLivesDelta": target_lives_delta,
                    "livesDelta": source_lives_delta,
                    "cardsLost": source_removed,
                    "cardsGained": source_added,
                }

            self._publish_team_general_message(
                game_id=game_id,
                team_id=source_team_id,
                message="",
                message_key=source_message_key,
                message_params=source_params,
                title_key="teamDashboard.popupTitle",
            )

    def _publish_action_added_events(self, *, db: DbSession, game_id: str, action: Dict[str, Any]) -> None:
        payload = self._action_payload(action)
        target_team_id = str(payload.get("target_team_id") or "")
        if not target_team_id:
            return

        requested_card_type = str(payload.get("requested_card_type") or "").strip()
        if not requested_card_type:
            card_id = str(payload.get("card_id") or "").strip()
            if card_id:
                card = self._repository.getCardByGameIdAndCardId(db, game_id, card_id)
                requested_card_type = str((card or {}).get("type") or "").strip()
        payload["requested_card_type"] = requested_card_type or None

        self._ws_publisher.publish(
            event="admin.exploding_kittens.action.add",
            payload=payload,
            channels=[f"channel:{game_id}:admin"],
        )
        self._ws_publisher.publish(
            event="team.exploding_kittens.action.add",
            payload=payload,
            channels=[f"channel:{game_id}:{target_team_id}"],
        )

        source_team_id = str(payload.get("source_team_id") or "").strip()
        source_team = self._repository.getTeamByGameIdAndTeamId(db, game_id, source_team_id) if source_team_id else None
        source_name = str((source_team or {}).get("name") or source_team_id or "Another team")
        action_label = self._describe_action_type(action)
        self._publish_team_general_message(
            game_id=game_id,
            team_id=target_team_id,
            message="",
            message_key="teamDashboard.popup.actionTargeted",
            message_params={
                "team": source_name,
                "event": str(action.get("action_type") or ""),
                "requestedCardType": requested_card_type,
                "requested_card_type": requested_card_type,
            },
            title_key="teamDashboard.popupTitle",
        )

        if source_team_id and source_team_id != target_team_id:
            target_team = self._repository.getTeamByGameIdAndTeamId(db, game_id, target_team_id)
            target_name = str((target_team or {}).get("name") or target_team_id or "Another team")
            self._publish_team_general_message(
                game_id=game_id,
                team_id=source_team_id,
                message="",
                message_key="teamDashboard.popup.actionPendingSource",
                message_params={
                    "team": target_name,
                    "event": str(action.get("action_type") or ""),
                    "requestedCardType": requested_card_type,
                    "requested_card_type": requested_card_type,
                },
                title_key="teamDashboard.popupTitle",
            )

    def _publish_action_removed_events(
        self,
        *,
        game_id: str,
        action_id: str,
        target_team_id: str,
        status_value: str,
    ) -> None:
        normalized_action_id = str(action_id or "").strip()
        normalized_target_team_id = str(target_team_id or "").strip()
        if not normalized_action_id or not normalized_target_team_id:
            return

        payload = {
            "id": normalized_action_id,
            "game_id": game_id,
            "target_team_id": normalized_target_team_id,
            "status": str(status_value or "resolved"),
        }
        self._ws_publisher.publish(
            event="admin.exploding_kittens.action.remove",
            payload=payload,
            channels=[f"channel:{game_id}:admin"],
        )
        self._ws_publisher.publish(
            event="team.exploding_kittens.action.remove",
            payload=payload,
            channels=[f"channel:{game_id}:{normalized_target_team_id}"],
        )

    def _get_pending_state_flags(self, team: Optional[Dict[str, Any]]) -> Dict[str, bool]:
        team_data = team if isinstance(team, dict) else {}
        return {
            state: bool(team_data.get(flag_name))
            for flag_name, state in self._STATE_FLAG_TO_KEY.items()
        }

    def _publish_state_change_event(
        self,
        *,
        game_id: str,
        team_id: str,
        state: str,
        active: bool,
    ) -> None:
        suffix = "activate" if active else "deactivate"
        self._ws_publisher.publish(
            event=f"admin.exploding_kittens.state.{suffix}",
            payload={
                "team_id": team_id,
                "state": state,
            },
            channels=[f"channel:{game_id}:admin"],
        )
        self._ws_publisher.publish(
            event=f"team.exploding_kittens.state.{suffix}",
            payload={
                "state": state,
            },
            channels=[f"channel:{game_id}:{team_id}"],
        )

    def _publish_pending_state_transition_events(
        self,
        *,
        game_id: str,
        team_id: str,
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
    ) -> None:
        before_flags = self._get_pending_state_flags(before)
        after_flags = self._get_pending_state_flags(after)
        for state in self._STATE_FLAG_TO_KEY.values():
            was_active = bool(before_flags.get(state))
            is_active = bool(after_flags.get(state))
            if was_active == is_active:
                continue
            self._publish_state_change_event(
                game_id=game_id,
                team_id=team_id,
                state=state,
                active=is_active,
            )

    @staticmethod
    def _extract_team_lives(team: Optional[Dict[str, Any]]) -> Optional[int]:
        if not isinstance(team, dict):
            return None
        try:
            return int(team.get("lives") or 0)
        except (TypeError, ValueError):
            return None

    def _publish_lives_updated_event(
        self,
        *,
        game_id: str,
        team_id: str,
        lives: int,
    ) -> None:
        safe_lives = max(0, int(lives))
        self._ws_publisher.publish(
            event="game.exploding_kittens.highscore.adjust",
            payload={
                "team_id": team_id,
                "lives": safe_lives,
            },
            channels=[f"channel:{game_id}"],
        )
        self._ws_publisher.publish(
            event="admin.exploding_kittens.lives.updated",
            payload={
                "team_id": team_id,
                "lives": safe_lives,
            },
            channels=[f"channel:{game_id}:admin"],
        )
        self._ws_publisher.publish(
            event="team.exploding_kittens.lives.updated",
            payload={
                "lives": safe_lives,
            },
            channels=[f"channel:{game_id}:{team_id}"],
        )

    def _publish_lives_transition_event(
        self,
        *,
        game_id: str,
        team_id: str,
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
    ) -> None:
        before_lives = self._extract_team_lives(before)
        after_lives = self._extract_team_lives(after)
        if before_lives is None or after_lives is None or before_lives == after_lives:
            return
        self._publish_lives_updated_event(
            game_id=game_id,
            team_id=team_id,
            lives=after_lives,
        )

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/exploding-kittens", tags=["exploding-kittens"])

        @router.get(
            "/{game_id}/cards",
            response_model=ExplodingKittensCardsResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List cards",
        )
        def list_cards(game_id: str, principal: CurrentPrincipal, db: DbSession) -> ExplodingKittensCardsResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            cards = self._repository.fetchCardsByGameId(db, game_id)
            return ExplodingKittensCardsResponse(cards=cards)

        @router.get(
            "/{game_id}/cards/{card_id}",
            response_model=ExplodingKittensCardResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get card",
        )
        def get_card(game_id: str, card_id: str, principal: CurrentPrincipal, db: DbSession) -> ExplodingKittensCardResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            card = self._repository.getCardByGameIdAndCardId(db, game_id, card_id)
            if card is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="explodingKittens.cards.notFound")
            return ExplodingKittensCardResponse(card=card)

        @router.post(
            "/{game_id}/cards/bulk-add",
            response_model=MessageKeyResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Bulk add cards by type",
        )
        def add_cards(
            game_id: str,
            body: AddCardsRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> MessageKeyResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            try:
                self._service.addCardsByType(
                    db,
                    game_id=game_id,
                    card_type=body.card_type.strip(),
                    quantity=body.quantity,
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.cards.addFailed") from error

            return MessageKeyResponse(
                message_key=translate_value("explodingKittens.cards.addSuccess", locale=locale)
            )

        @router.post(
            "/{game_id}/cards",
            response_model=ExplodingKittensCardResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Create card",
        )
        def create_card(
            game_id: str,
            body: CardCreateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> ExplodingKittensCardResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            try:
                card = self._service.createCard(
                    db,
                    game_id=game_id,
                    card_type=body.card_type.strip(),
                    title=body.title,
                    image_path=body.image_path,
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.cards.createFailed") from error

            return ExplodingKittensCardResponse(card=card)

        @router.put(
            "/{game_id}/cards/{card_id}",
            response_model=ExplodingKittensCardResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update card",
        )
        def update_card(
            game_id: str,
            card_id: str,
            body: CardUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> ExplodingKittensCardResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            try:
                card = self._service.updateCard(
                    db,
                    game_id=game_id,
                    card_id=card_id,
                    card_type=body.card_type.strip() if body.card_type else None,
                    title=body.title,
                    image_path=body.image_path,
                    holder_team_id=body.holder_team_id,
                )
            except ValueError as error:
                status_code = status.HTTP_404_NOT_FOUND if str(error) == "explodingKittens.cards.notFound" else status.HTTP_400_BAD_REQUEST
                raise HTTPException(status_code=status_code, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.cards.updateFailed") from error

            return ExplodingKittensCardResponse(card=card)

        @router.delete(
            "/{game_id}/cards/{card_id}",
            response_model=MessageKeyResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Delete card",
        )
        def delete_card(game_id: str, card_id: str, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> MessageKeyResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            try:
                self._service.deleteCard(db, game_id=game_id, card_id=card_id)
            except ValueError as error:
                status_code = status.HTTP_404_NOT_FOUND if str(error) == "explodingKittens.cards.notFound" else status.HTTP_400_BAD_REQUEST
                raise HTTPException(status_code=status_code, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.cards.deleteFailed") from error

            return MessageKeyResponse(
                message_key=translate_value("explodingKittens.cards.deleteSuccess", locale=locale)
            )

        @router.post(
            "/{game_id}/cards/pdf",
            summary=f"{ACCESS_ADMIN_LABEL} Export cards QR PDF",
        )
        def export_cards_pdf(
            game_id: str,
            principal: CurrentPrincipal,
            db: DbSession,
            per_row: int = Form(default=3, ge=1, le=8),
            rows_per_page: int = Form(default=8, ge=1, le=20),
            include_final_url: bool = Form(default=False),
            center_logo: UploadFile | None = File(default=None),
        ) -> Response:
            game = self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            try:
                import qrcode  # pyright: ignore[reportMissingImports]
                from reportlab.lib.pagesizes import A4  # pyright: ignore[reportMissingImports]
                from reportlab.lib.utils import ImageReader  # pyright: ignore[reportMissingImports]
                from reportlab.pdfgen import canvas  # pyright: ignore[reportMissingImports]
                from PIL import Image  # pyright: ignore[reportMissingImports]
            except ImportError as error:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="explodingKittens.cards.pdfDependenciesMissing",
                ) from error

            cards = self._repository.fetchCardsByGameId(db, game_id)
            per_row = int(per_row)
            rows_per_page = int(rows_per_page)
            include_final_url = bool(include_final_url)

            center_logo_bytes: bytes | None = None
            if center_logo is not None:
                mime_type = str(center_logo.content_type or "").lower()
                if not mime_type.startswith("image/"):
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.cards.pdfLogoInvalid")
                logo_bytes = center_logo.file.read()
                if len(logo_bytes) > 2 * 1024 * 1024:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.cards.pdfLogoTooLarge")
                center_logo_bytes = logo_bytes

            settings = get_settings()
            app_base_url = settings.app_public_base_url.rstrip("/")

            page_width_pt, page_height_pt = A4
            usable_width_pt = page_width_pt
            usable_height_pt = page_height_pt
            row_height_pt = usable_height_pt / rows_per_page
            column_width_pt = usable_width_pt / per_row
            cell_padding_pt = 6.0
            text_reserve_pt = 38.0 if include_final_url else 18.0
            qr_edge_pt = max(
                48.0,
                min(
                    column_width_pt - (cell_padding_pt * 2),
                    row_height_pt - text_reserve_pt - (cell_padding_pt * 2),
                ),
            )
            qr_size_px = max(220, min(1400, int(round(qr_edge_pt * 2.4))))

            output = io.BytesIO()
            pdf = canvas.Canvas(output, pagesize=A4)

            cards_per_page = max(1, per_row * rows_per_page)

            for index, card in enumerate(cards):
                index_in_page = index % cards_per_page
                row_index = index_in_page // per_row
                column_index = index_in_page % per_row

                if index > 0 and index_in_page == 0:
                    pdf.showPage()

                scan_url = f"{app_base_url}/team/scan/{str(card.get('qr_token') or '').strip()}"
                qr_image = qrcode.make(scan_url).convert("RGBA")

                if center_logo_bytes:
                    logo = Image.open(io.BytesIO(center_logo_bytes)).convert("RGBA")
                    target_edge = max(24, int(min(qr_image.width, qr_image.height) * 0.24))
                    logo.thumbnail((target_edge, target_edge), Image.Resampling.LANCZOS)
                    logo_x = (qr_image.width - logo.width) // 2
                    logo_y = (qr_image.height - logo.height) // 2
                    qr_image.alpha_composite(logo, (logo_x, logo_y))

                qr_bytes = io.BytesIO()
                qr_image.save(qr_bytes, format="PNG")
                qr_bytes.seek(0)

                cell_left = column_index * column_width_pt
                cell_top = page_height_pt - (row_index * row_height_pt)

                qr_x = cell_left + ((column_width_pt - qr_edge_pt) / 2)
                qr_y = cell_top - cell_padding_pt - qr_edge_pt

                pdf.setLineWidth(0.2)
                pdf.setStrokeGray(0.75)
                pdf.rect(cell_left, cell_top - row_height_pt, column_width_pt, row_height_pt)

                pdf.drawImage(ImageReader(qr_bytes), qr_x, qr_y, width=qr_edge_pt, height=qr_edge_pt, preserveAspectRatio=True)

                if include_final_url:
                    pdf.setFillGray(0.15)
                    pdf.setFont("Helvetica", 7)
                    max_text_width = column_width_pt - (cell_padding_pt * 2)
                    text = scan_url
                    while len(text) > 8 and pdf.stringWidth(text, "Helvetica", 7) > max_text_width:
                        text = f"{text[:-4]}…"
                    text_x = cell_left + cell_padding_pt
                    text_y = max(cell_top - row_height_pt + cell_padding_pt, qr_y - 10)
                    pdf.drawString(text_x, text_y, text)

            if not cards:
                pdf.setLineWidth(0.2)
                pdf.setStrokeGray(0.75)
                pdf.rect(24, page_height_pt - 120, page_width_pt - 48, 96)
            pdf.save()
            output.seek(0)

            game_code = str(game.get("code") or game_id)
            filename = f"game-{game_code}-qr-codes.pdf"

            return Response(
                content=output.getvalue(),
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        @router.get(
            "/{game_id}/teams/{team_id}/state",
            response_model=ExplodingKittensStateResponse,
            summary=f"{ACCESS_BOTH_LABEL} Get team state",
        )
        def get_team_state(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> ExplodingKittensStateResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)

            try:
                state = self._service.getTeamState(db, game_id=game_id, team_id=team_id)
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

            return ExplodingKittensStateResponse(
                state=state,
            )

        @router.get(
            "/{game_id}/actions/pending",
            response_model=PendingActionsResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List pending actions",
        )
        def list_pending_actions(game_id: str, principal: CurrentPrincipal, db: DbSession) -> PendingActionsResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            actions = self._repository.fetchPendingActionsByGame(db, game_id=game_id)
            return PendingActionsResponse(actions=[self._action_payload(action) for action in actions])

        @router.post(
            "/{game_id}/teams/{team_id}/cards/{card_id}/play",
            response_model=PlayCardResponse,
            summary=f"{ACCESS_BOTH_LABEL} Play hand card",
        )
        def play_card(
            game_id: str,
            team_id: str,
            card_id: str,
            body: PlayCardRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> PlayCardResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            team_before = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            team_hand_before = self._repository.fetchHandCardsByTeamId(db, team_id)

            try:
                result = self._service.playCard(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    card_id=card_id,
                    target_team_id=body.target_team_id,
                )
            except ValueError as error:
                status_code = status.HTTP_404_NOT_FOUND if str(error) in {"team.notFound", "explodingKittens.cards.notFound"} else status.HTTP_400_BAD_REQUEST
                raise HTTPException(status_code=status_code, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.play.failed") from error

            team_after = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            team_hand_after = self._repository.fetchHandCardsByTeamId(db, team_id)
            self._publish_pending_state_transition_events(
                game_id=game_id,
                team_id=team_id,
                before=team_before,
                after=team_after,
            )
            self._publish_team_hand_diff_events(
                game_id=game_id,
                team_id=team_id,
                before_cards=team_hand_before,
                after_cards=team_hand_after,
            )
            self._publish_lives_transition_event(
                game_id=game_id,
                team_id=team_id,
                before=team_before,
                after=team_after,
            )

            action_id = str((result.extra or {}).get("action_id") or "").strip()
            if action_id:
                created_action = self._repository.getPendingActionByIdForTeam(
                    db,
                    action_id=action_id,
                    game_id=game_id,
                    target_team_id=str((result.extra or {}).get("target_team_id") or "").strip(),
                )
                if created_action is not None:
                    self._publish_action_added_events(db=db, game_id=game_id, action=created_action)

            return PlayCardResponse(
                success=result.status == "ok",
                message_key=translate_value(result.message_key, locale=locale),
                action_type=str((result.extra or {}).get("action_type") or "unknown"),
            )

        @router.post(
            "/{game_id}/teams/{team_id}/scan",
            response_model=ScanResponse,
            summary=f"{ACCESS_BOTH_LABEL} Scan card",
        )
        def scan_card(
            game_id: str,
            team_id: str,
            body: ScanCardRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> ScanResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            team_before = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            team_hand_before = self._repository.fetchHandCardsByTeamId(db, team_id)
            requested_target_team_id = str(body.target_team_id or "").strip()
            target_team_before = None
            target_team_hand_before: list[Dict[str, Any]] = []
            if requested_target_team_id and requested_target_team_id != team_id:
                target_team_before = self._repository.getTeamByGameIdAndTeamId(db, game_id, requested_target_team_id)
                target_team_hand_before = self._repository.fetchHandCardsByTeamId(db, requested_target_team_id)

            try:
                result = self._service.scanCard(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    qr_token=body.qr_token,
                    target_team_id=body.target_team_id,
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.scan.failed") from error

            team_after = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            team_hand_after = self._repository.fetchHandCardsByTeamId(db, team_id)
            self._publish_pending_state_transition_events(
                game_id=game_id,
                team_id=team_id,
                before=team_before,
                after=team_after,
            )
            self._publish_team_hand_diff_events(
                game_id=game_id,
                team_id=team_id,
                before_cards=team_hand_before,
                after_cards=team_hand_after,
            )
            self._publish_lives_transition_event(
                game_id=game_id,
                team_id=team_id,
                before=team_before,
                after=team_after,
            )

            affected_target_team_id = str((result.extra or {}).get("target_team_id") or "").strip()
            if affected_target_team_id and affected_target_team_id != team_id and target_team_before is not None:
                target_team_after = self._repository.getTeamByGameIdAndTeamId(db, game_id, affected_target_team_id)
                target_team_hand_after = self._repository.fetchHandCardsByTeamId(db, affected_target_team_id)
                self._publish_team_hand_diff_events(
                    game_id=game_id,
                    team_id=affected_target_team_id,
                    before_cards=target_team_hand_before,
                    after_cards=target_team_hand_after,
                )
                self._publish_lives_transition_event(
                    game_id=game_id,
                    team_id=affected_target_team_id,
                    before=target_team_before,
                    after=target_team_after,
                )

            action_id = str((result.extra or {}).get("action_id") or "").strip()
            if action_id:
                created_action = self._repository.getPendingActionByIdForTeam(
                    db,
                    action_id=action_id,
                    game_id=game_id,
                    target_team_id=affected_target_team_id,
                )
                if created_action is None:
                    created_action = self._repository.getPendingActionById(
                        db,
                        action_id=action_id,
                        game_id=game_id,
                    )
                if created_action is not None:
                    self._publish_action_added_events(db=db, game_id=game_id, action=created_action)

            return ScanResponse(
                success=result.status == "ok",
                status=result.status,
                message_key=translate_value(result.message_key, locale=locale),
                card=result.card,
                pending_state=(result.extra or {}).get("state"),
            )

        @router.post(
            "/{game_id}/teams/{team_id}/state/resolve",
            response_model=ResolveStateResponse,
            summary=f"{ACCESS_BOTH_LABEL} Resolve pending state",
        )
        def resolve_state(
            game_id: str,
            team_id: str,
            body: ResolveStateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> ResolveStateResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            team_before = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            team_hand_before = self._repository.fetchHandCardsByTeamId(db, team_id)
            requested_target_team_id = str(body.target_team_id or "").strip()
            target_team_before = None
            target_team_hand_before: list[Dict[str, Any]] = []
            if requested_target_team_id and requested_target_team_id != team_id:
                target_team_before = self._repository.getTeamByGameIdAndTeamId(db, game_id, requested_target_team_id)
                target_team_hand_before = self._repository.fetchHandCardsByTeamId(db, requested_target_team_id)

            try:
                result = self._service.resolveState(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    resolve_skip=body.resolve_skip,
                    confirm_peek=body.confirm_peek,
                    reject_peek=body.reject_peek,
                    qr_token=body.qr_token,
                    target_team_id=body.target_team_id,
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.state.resolveFailed") from error

            team_after = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            team_hand_after = self._repository.fetchHandCardsByTeamId(db, team_id)
            self._publish_pending_state_transition_events(
                game_id=game_id,
                team_id=team_id,
                before=team_before,
                after=team_after,
            )
            self._publish_team_hand_diff_events(
                game_id=game_id,
                team_id=team_id,
                before_cards=team_hand_before,
                after_cards=team_hand_after,
            )
            self._publish_lives_transition_event(
                game_id=game_id,
                team_id=team_id,
                before=team_before,
                after=team_after,
            )

            affected_target_team_id = str((result.extra or {}).get("target_team_id") or "").strip()
            if affected_target_team_id and affected_target_team_id != team_id and target_team_before is not None:
                target_team_after = self._repository.getTeamByGameIdAndTeamId(db, game_id, affected_target_team_id)
                target_team_hand_after = self._repository.fetchHandCardsByTeamId(db, affected_target_team_id)
                self._publish_team_hand_diff_events(
                    game_id=game_id,
                    team_id=affected_target_team_id,
                    before_cards=target_team_hand_before,
                    after_cards=target_team_hand_after,
                )
                self._publish_lives_transition_event(
                    game_id=game_id,
                    team_id=affected_target_team_id,
                    before=target_team_before,
                    after=target_team_after,
                )

            action_id = str((result.extra or {}).get("action_id") or "").strip()
            if action_id:
                created_action = self._repository.getPendingActionByIdForTeam(
                    db,
                    action_id=action_id,
                    game_id=game_id,
                    target_team_id=affected_target_team_id,
                )
                if created_action is None:
                    created_action = self._repository.getPendingActionById(
                        db,
                        action_id=action_id,
                        game_id=game_id,
                    )
                if created_action is not None:
                    self._publish_action_added_events(db=db, game_id=game_id, action=created_action)

            return ResolveStateResponse(
                success=result.status == "ok",
                status=result.status,
                message_key=translate_value(result.message_key, locale=locale),
                pending_state=(result.extra or {}).get("state"),
            )

        @router.post(
            "/{game_id}/teams/{team_id}/actions/{action_id}/resolve",
            response_model=ResolveActionResponse,
            summary=f"{ACCESS_BOTH_LABEL} Resolve action",
        )
        def resolve_action(
            game_id: str,
            team_id: str,
            action_id: str,
            body: ResolveActionRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> ResolveActionResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            pending_action = self._repository.getPendingActionByIdForTeam(
                db,
                action_id=action_id,
                game_id=game_id,
                target_team_id=team_id,
            )
            team_before = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            target_hand_before = self._repository.fetchHandCardsByTeamId(db, team_id)
            source_team_id = str((pending_action or {}).get("source_team_id") or "").strip()
            source_team_before = None
            source_hand_before: list[Dict[str, Any]] = []
            source_team_after = None
            source_hand_after: list[Dict[str, Any]] = []
            if source_team_id and source_team_id != team_id:
                source_team_before = self._repository.getTeamByGameIdAndTeamId(db, game_id, source_team_id)
                source_hand_before = self._repository.fetchHandCardsByTeamId(db, source_team_id)

            try:
                result = self._service.resolveAction(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    action_id=action_id,
                    use_nope=body.use_nope,
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.action.resolveFailed") from error

            team_after = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            target_hand_after = self._repository.fetchHandCardsByTeamId(db, team_id)
            self._publish_team_hand_diff_events(
                game_id=game_id,
                team_id=team_id,
                before_cards=target_hand_before,
                after_cards=target_hand_after,
            )
            self._publish_lives_transition_event(
                game_id=game_id,
                team_id=team_id,
                before=team_before,
                after=team_after,
            )

            if source_team_id and source_team_before is not None:
                source_team_after = self._repository.getTeamByGameIdAndTeamId(db, game_id, source_team_id)
                source_hand_after = self._repository.fetchHandCardsByTeamId(db, source_team_id)
                self._publish_team_hand_diff_events(
                    game_id=game_id,
                    team_id=source_team_id,
                    before_cards=source_hand_before,
                    after_cards=source_hand_after,
                )
                self._publish_lives_transition_event(
                    game_id=game_id,
                    team_id=source_team_id,
                    before=source_team_before,
                    after=source_team_after,
                )
            else:
                source_team_after = source_team_before
                source_hand_after = source_hand_before

            self._publish_action_removed_events(
                game_id=game_id,
                action_id=action_id,
                target_team_id=team_id,
                status_value="canceled" if body.use_nope else "resolved",
            )

            if pending_action is not None:
                self._publish_action_resolution_messages(
                    game_id=game_id,
                    action=pending_action,
                    use_nope=bool(body.use_nope),
                    target_team_before=team_before,
                    target_team_after=team_after,
                    source_team_before=source_team_before,
                    source_team_after=source_team_after,
                    target_hand_before=target_hand_before,
                    target_hand_after=target_hand_after,
                    source_hand_before=source_hand_before,
                    source_hand_after=source_hand_after,
                )

            return ResolveActionResponse(
                success=result.status == "ok",
                status=result.status,
                message_key=translate_value(result.message_key, locale=locale),
                card_type=(result.extra or {}).get("card_type"),
            )

        @router.post(
            "/{game_id}/teams/{team_id}/combos/use",
            response_model=UseComboResponse,
            summary=f"{ACCESS_BOTH_LABEL} Use combo",
        )
        def use_combo(
            game_id: str,
            team_id: str,
            body: UseComboRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> UseComboResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            team_hand_before = self._repository.fetchHandCardsByTeamId(db, team_id)

            try:
                result = self._service.useCombo(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    card_ids=body.card_ids,
                    target_team_id=body.target_team_id,
                    requested_card_type=body.requested_card_type,
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.combo.failed") from error

            team_hand_after = self._repository.fetchHandCardsByTeamId(db, team_id)
            self._publish_team_hand_diff_events(
                game_id=game_id,
                team_id=team_id,
                before_cards=team_hand_before,
                after_cards=team_hand_after,
            )

            action_id = str((result.extra or {}).get("action_id") or "").strip()
            if action_id:
                target_team_id = str((result.extra or {}).get("target_team_id") or "").strip()
                created_action = self._repository.getPendingActionByIdForTeam(
                    db,
                    action_id=action_id,
                    game_id=game_id,
                    target_team_id=target_team_id,
                )
                if created_action is not None:
                    self._publish_action_added_events(db=db, game_id=game_id, action=created_action)

            return UseComboResponse(
                success=result.status == "ok",
                combo_type=str((result.extra or {}).get("combo_type") or "unknown"),
            )

        @router.post(
            "/{game_id}/teams/{team_id}/hand/add-random",
            response_model=AddRandomTeamHandCardResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Add random hand card by type",
        )
        def add_random_team_hand_card(
            game_id: str,
            team_id: str,
            body: RemoveRandomTeamHandCardRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> AddRandomTeamHandCardResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            try:
                result = self._service.addRandomTeamHandCardByType(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    card_type=body.card_type.strip(),
                )
            except ValueError as error:
                status_code = status.HTTP_404_NOT_FOUND if str(error) == "team.notFound" else status.HTTP_400_BAD_REQUEST
                raise HTTPException(status_code=status_code, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.hand.addFailed") from error

            normalized_card_type = body.card_type.strip()
            amount = self._count_team_hand_cards_by_type(db, team_id, normalized_card_type)
            self._publish_hand_adjusted_amount_event(
                game_id=game_id,
                team_id=team_id,
                card_type=normalized_card_type,
                amount=amount,
            )

            added_card_id = str(result.get("card_id") or "").strip()
            if added_card_id:
                added_card = self._repository.getCardByGameIdAndCardId(db, game_id, added_card_id)
                if added_card:
                    self._ws_publisher.publish(
                        event="team.exploding_kittens.card.add",
                        payload={
                            "game_id": game_id,
                            "team_id": team_id,
                            "name": str(added_card.get("title") or ""),
                            "type": str(added_card.get("type") or normalized_card_type),
                            "image": str(added_card.get("image_path") or ""),
                        },
                        channels=[f"channel:{game_id}:{team_id}"],
                    )

            return AddRandomTeamHandCardResponse(
                team_id=team_id,
                card_type=normalized_card_type,
                added=bool(result.get("added")),
                card_id=result.get("card_id"),
            )

        @router.post(
            "/{game_id}/teams/{team_id}/hand/remove-random",
            response_model=RemoveRandomTeamHandCardResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Remove random hand card by type",
        )
        def remove_random_team_hand_card(
            game_id: str,
            team_id: str,
            body: RemoveRandomTeamHandCardRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> RemoveRandomTeamHandCardResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            try:
                result = self._service.removeRandomTeamHandCardByType(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    card_type=body.card_type.strip(),
                )
            except ValueError as error:
                status_code = status.HTTP_404_NOT_FOUND if str(error) == "team.notFound" else status.HTTP_400_BAD_REQUEST
                raise HTTPException(status_code=status_code, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.hand.removeFailed") from error

            normalized_card_type = body.card_type.strip()
            amount = self._count_team_hand_cards_by_type(db, team_id, normalized_card_type)
            self._publish_hand_adjusted_amount_event(
                game_id=game_id,
                team_id=team_id,
                card_type=normalized_card_type,
                amount=amount,
            )

            removed_card_id = str(result.get("card_id") or "").strip()
            if removed_card_id:
                self._ws_publisher.publish(
                    event="team.exploding_kittens.card.remove",
                    payload={
                        "game_id": game_id,
                        "team_id": team_id,
                        "id": removed_card_id,
                    },
                    channels=[f"channel:{game_id}:{team_id}"],
                )

            return RemoveRandomTeamHandCardResponse(
                team_id=team_id,
                card_type=normalized_card_type,
                removed=bool(result.get("removed")),
                card_id=result.get("card_id"),
            )

        @router.post(
            "/{game_id}/teams/{team_id}/lives/adjust",
            response_model=ExplodingKittensLivesResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Adjust team lives",
        )
        def adjust_team_lives(
            game_id: str,
            team_id: str,
            body: AdjustLivesRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> ExplodingKittensLivesResponse:
            self._require_exploding_kittens_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            try:
                result = self._service.adjustTeamLives(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    delta=body.delta,
                )
            except ValueError as error:
                status_code = status.HTTP_404_NOT_FOUND if str(error) == "team.notFound" else status.HTTP_400_BAD_REQUEST
                raise HTTPException(status_code=status_code, detail=str(error)) from error
            except Exception as error:
                self._repository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="explodingKittens.lives.updateFailed") from error

            self._publish_lives_updated_event(
                game_id=game_id,
                team_id=team_id,
                lives=int((result.extra or {}).get("lives") or 0),
            )

            return ExplodingKittensLivesResponse(
                team_id=team_id,
                lives=int((result.extra or {}).get("lives") or 0),
            )

        return router
