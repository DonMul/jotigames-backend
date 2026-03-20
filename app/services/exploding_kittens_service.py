import json
import random
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from app.dependencies import DbSession
from app.repositories.exploding_kittens_repository import ExplodingKittensRepository
from app.services.exploding_kittens_image_picker import ExplodingKittensImagePicker

_SCANNABLE_TO_HAND = {
    "attack",
    "defuse",
    "favor",
    "nope",
    "see_the_future",
    "shuffle",
    "skip",
    "random1",
    "random2",
    "random3",
    "random4",
    "random5",
}

_VALID_CARD_TYPES = {
    "attack",
    "defuse",
    "exploding_kitten",
    "favor",
    "felix",
    "nope",
    "see_the_future",
    "shuffle",
    "skip",
    "random1",
    "random2",
    "random3",
    "random4",
    "random5",
}

_ACTION_PENDING = "pending"
_ACTION_RESOLVED = "resolved"
_ACTION_CANCELED = "canceled"

_ACTION_ATTACK = "attack"
_ACTION_FAVOR = "favor"
_ACTION_COMBO_TWO_SAME = "combo_two_same"
_ACTION_COMBO_THREE_SAME = "combo_three_same"


@dataclass
class ExplodingKittensResult:
    status: str
    message_key: str
    state: Dict[str, Any]
    card: Optional[Dict[str, Any]] = None
    extra: Optional[Dict[str, Any]] = None


class ExplodingKittensService:
    def __init__(self) -> None:
        self._repository = ExplodingKittensRepository()
        self._imagePicker = ExplodingKittensImagePicker()

    def addCardsByType(self, db: DbSession, *, game_id: str, card_type: str, quantity: int) -> int:
        if card_type not in _VALID_CARD_TYPES:
            raise ValueError("explodingKittens.cards.invalidType")

        amount = max(1, min(quantity, 200))
        created_at = datetime.now(UTC).replace(tzinfo=None)
        cards = []
        for _ in range(amount):
            cards.append(
                {
                    "id": str(uuid4()),
                    "game_id": game_id,
                    "type": card_type,
                    "title": f"card.type.{card_type}",
                    "qr_token": secrets.token_hex(16),
                    "image_path": self._imagePicker.pickRandomForType(card_type),
                    "holder_team_id": None,
                    "locked": False,
                    "created_at": created_at,
                }
            )

        self._repository.createCardsByValuesWithoutCommit(db, cards)
        self._repository.commitChanges(db)
        return amount

    def createCard(self, db: DbSession, *, game_id: str, card_type: str, title: Optional[str], image_path: Optional[str]) -> Dict[str, Any]:
        if card_type not in _VALID_CARD_TYPES:
            raise ValueError("explodingKittens.cards.invalidType")

        card_id = str(uuid4())
        selected_image = image_path if image_path is not None else self._imagePicker.pickRandomForType(card_type)
        self._repository.createCardByValuesWithoutCommit(
            db,
            {
                "id": card_id,
                "game_id": game_id,
                "type": card_type,
                "title": title if title else f"card.type.{card_type}",
                "qr_token": secrets.token_hex(16),
                "image_path": selected_image,
                "holder_team_id": None,
                "locked": False,
                "created_at": datetime.now(UTC).replace(tzinfo=None),
            },
        )
        self._repository.commitChanges(db)

        created = self._repository.getCardByGameIdAndCardId(db, game_id, card_id)
        if created is None:
            raise ValueError("explodingKittens.cards.notFound")
        return created

    def updateCard(
        self,
        db: DbSession,
        *,
        game_id: str,
        card_id: str,
        card_type: Optional[str],
        title: Optional[str],
        image_path: Optional[str],
        holder_team_id: Optional[str],
    ) -> Dict[str, Any]:
        current = self._repository.getCardByGameIdAndCardId(db, game_id, card_id)
        if current is None:
            raise ValueError("explodingKittens.cards.notFound")

        values: Dict[str, Any] = {}
        if card_type is not None:
            if card_type not in _VALID_CARD_TYPES:
                raise ValueError("explodingKittens.cards.invalidType")
            values["type"] = card_type
            if title is None:
                values["title"] = f"card.type.{card_type}"
            if image_path is None:
                values["image_path"] = self._imagePicker.pickRandomForType(card_type)
        if title is not None:
            values["title"] = title
        if image_path is not None:
            values["image_path"] = image_path
        if holder_team_id is not None:
            values["holder_team_id"] = holder_team_id

        self._repository.updateCardByGameIdAndCardIdWithoutCommit(db, game_id, card_id, values)
        self._repository.commitChanges(db)

        updated = self._repository.getCardByGameIdAndCardId(db, game_id, card_id)
        if updated is None:
            raise ValueError("explodingKittens.cards.notFound")
        return updated

    def deleteCard(self, db: DbSession, *, game_id: str, card_id: str) -> None:
        card = self._repository.getCardByGameIdAndCardId(db, game_id, card_id)
        if card is None:
            raise ValueError("explodingKittens.cards.notFound")
        if card.get("holder_team_id") is not None or bool(card.get("locked")):
            raise ValueError("explodingKittens.cards.removeBlocked")

        self._repository.deleteCardByGameIdAndCardIdWithoutCommit(db, game_id, card_id)
        self._repository.commitChanges(db)

    def getTeamState(self, db: DbSession, *, game_id: str, team_id: str) -> Dict[str, Any]:
        team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
        if team is None:
            raise ValueError("team.notFound")

        hand = self._repository.fetchHandCardsByTeamId(db, team_id)
        pending_actions = self._repository.fetchPendingActionsByTeam(db, game_id=game_id, target_team_id=team_id)
        pending_actions = [
            self._enrichPendingActionForClient(db, game_id=game_id, action=action)
            for action in pending_actions
        ]

        return {
            "team_id": team_id,
            "lives": int(team.get("lives") or 0),
            "pending_attack": bool(team.get("pending_attack")),
            "pending_peek": bool(team.get("pending_peek")),
            "pending_skip": bool(team.get("pending_skip")),
            "hand": hand,
            "pending_actions": pending_actions,
        }

    def _enrichPendingActionForClient(self, db: DbSession, *, game_id: str, action: Dict[str, Any]) -> Dict[str, Any]:
        action_data = dict(action or {})
        context_raw = action_data.get("context")
        requested_card_type = ""

        if isinstance(context_raw, dict):
            requested_card_type = str(context_raw.get("requestedCardType") or context_raw.get("requested_card_type") or "").strip()
        elif isinstance(context_raw, str) and context_raw.strip():
            try:
                parsed_context = json.loads(context_raw)
            except json.JSONDecodeError:
                parsed_context = None
            if isinstance(parsed_context, dict):
                requested_card_type = str(parsed_context.get("requestedCardType") or parsed_context.get("requested_card_type") or "").strip()

        if not requested_card_type:
            card_id = str(action_data.get("card_id") or "").strip()
            if card_id:
                card = self._repository.getCardByGameIdAndCardId(db, game_id, card_id)
                requested_card_type = str((card or {}).get("type") or "").strip()

        action_data["requested_card_type"] = requested_card_type or None
        return action_data

    def adjustTeamLives(self, db: DbSession, *, game_id: str, team_id: str, delta: int) -> ExplodingKittensResult:
        if delta == 0:
            raise ValueError("explodingKittens.lives.invalidDelta")

        team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
        if team is None:
            raise ValueError("team.notFound")

        current_lives = int(team.get("lives") or 0)
        updated_lives = max(0, current_lives + delta)

        self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(
            db,
            game_id,
            team_id,
            {"lives": updated_lives},
        )
        self._repository.commitChanges(db)

        return ExplodingKittensResult(
            status="ok",
            message_key="explodingKittens.lives.updated",
            state=self.getTeamState(db, game_id=game_id, team_id=team_id),
            extra={"delta": delta, "lives": updated_lives},
        )

    def playCard(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        card_id: str,
        target_team_id: Optional[str],
    ) -> ExplodingKittensResult:
        team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
        if team is None:
            raise ValueError("team.notFound")

        card = self._repository.getCardByGameIdAndCardId(db, game_id, card_id)
        if card is None:
            raise ValueError("explodingKittens.cards.notFound")

        holder_team_id = str(card.get("holder_team_id") or "")
        if holder_team_id != team_id:
            raise ValueError("explodingKittens.play.cardNotInHand")

        card_type = str(card.get("type") or "")
        if card_type == "attack":
            if bool(team.get("pending_attack")):
                raise ValueError("explodingKittens.play.attackAlreadyPending")

            self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                db,
                game_id,
                card_id,
                {"holder_team_id": None},
            )
            self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(
                db,
                game_id,
                team_id,
                {"pending_attack": True},
            )
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.play.attackReady",
                state={},
                extra={"action_type": "attack"},
            )

        if card_type == "favor":
            if not target_team_id:
                raise ValueError("explodingKittens.play.needsTarget")
            if target_team_id == team_id:
                raise ValueError("explodingKittens.play.targetSelf")

            target_team = self._repository.getTeamByGameIdAndTeamId(db, game_id, target_team_id)
            if target_team is None:
                raise ValueError("explodingKittens.play.targetInvalid")

            action_id = str(uuid4())
            self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                db,
                game_id,
                card_id,
                {"holder_team_id": None},
            )
            self._repository.createCardActionWithoutCommit(
                db,
                {
                    "id": action_id,
                    "game_id": game_id,
                    "source_team_id": team_id,
                    "target_team_id": target_team_id,
                    "card_id": None,
                    "action_type": _ACTION_FAVOR,
                    "status": _ACTION_PENDING,
                    "created_at": datetime.now(UTC).replace(tzinfo=None),
                    "resolved_at": None,
                    "context": json.dumps({"requestedCardType": str(card.get("type") or "")}),
                },
            )
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.play.favorTargeted",
                state={},
                extra={"action_type": "favor", "action_id": action_id, "target_team_id": target_team_id},
            )

        if card_type == "see_the_future":
            if bool(team.get("pending_peek")):
                raise ValueError("explodingKittens.play.peekAlreadyPending")

            self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                db,
                game_id,
                card_id,
                {"holder_team_id": None},
            )
            self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(
                db,
                game_id,
                team_id,
                {"pending_peek": True},
            )
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.play.peekReady",
                state={},
                extra={"action_type": "see_the_future"},
            )

        if card_type == "shuffle":
            self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                db,
                game_id,
                card_id,
                {"holder_team_id": None},
            )
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.play.shuffleDone",
                state={},
                extra={"action_type": "shuffle"},
            )

        if card_type == "skip":
            if bool(team.get("pending_skip")):
                raise ValueError("explodingKittens.play.skipAlreadyPending")

            self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                db,
                game_id,
                card_id,
                {"holder_team_id": None},
            )
            self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(
                db,
                game_id,
                team_id,
                {"pending_skip": True},
            )
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.play.skipReady",
                state={},
                extra={"action_type": "skip"},
            )

        raise ValueError("explodingKittens.play.cardNotPlayable")

    def removeRandomTeamHandCardByType(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        card_type: str,
    ) -> Dict[str, Any]:
        if card_type not in _VALID_CARD_TYPES:
            raise ValueError("explodingKittens.cards.invalidType")

        team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
        if team is None:
            raise ValueError("team.notFound")

        hand_cards = self._repository.fetchHandCardsByTeamId(db, team_id)
        candidates = [
            card for card in hand_cards if str(card.get("type") or "") == card_type and not bool(card.get("locked"))
        ]
        if not candidates:
            return {"removed": False, "card_id": None}

        selected = random.choice(candidates)
        selected_id = str(selected.get("id") or "")
        if not selected_id:
            return {"removed": False, "card_id": None}

        self._repository.updateCardByGameIdAndCardIdWithoutCommit(
            db,
            game_id,
            selected_id,
            {"holder_team_id": None},
        )
        self._repository.commitChanges(db)
        return {"removed": True, "card_id": selected_id}

    def addRandomTeamHandCardByType(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        card_type: str,
    ) -> Dict[str, Any]:
        if card_type not in _VALID_CARD_TYPES:
            raise ValueError("explodingKittens.cards.invalidType")

        team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
        if team is None:
            raise ValueError("team.notFound")

        available_cards = self._repository.fetchAvailableCardsByGameId(db, game_id)
        candidates = [
            card for card in available_cards if str(card.get("type") or "") == card_type and not bool(card.get("locked"))
        ]
        if not candidates:
            return {"added": False, "card_id": None}

        selected = random.choice(candidates)
        selected_id = str(selected.get("id") or "")
        if not selected_id:
            return {"added": False, "card_id": None}

        self._repository.updateCardByGameIdAndCardIdWithoutCommit(
            db,
            game_id,
            selected_id,
            {"holder_team_id": team_id},
        )
        self._repository.commitChanges(db)
        return {"added": True, "card_id": selected_id}

    def scanCard(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        qr_token: str,
        target_team_id: Optional[str],
    ) -> ExplodingKittensResult:
        team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
        if team is None:
            raise ValueError("team.notFound")

        card = self._repository.getCardByGameIdAndQrToken(db, game_id, qr_token)
        if card is None:
            raise ValueError("explodingKittens.scan.cardNotFound")

        if card.get("holder_team_id") is not None:
            return ExplodingKittensResult(
                status="error",
                message_key="explodingKittens.scan.cardInHand",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                card=card,
            )

        if bool(card.get("locked")):
            return ExplodingKittensResult(
                status="error",
                message_key="explodingKittens.scan.cardLocked",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                card=card,
            )

        if bool(team.get("pending_skip")):
            self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(
                db,
                game_id,
                team_id,
                {"pending_skip": False},
            )
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.state.skipResolved",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                card=card,
                extra={"state": "skip"},
            )

        if bool(team.get("pending_peek")):
            return ExplodingKittensResult(
                status="pending_state",
                message_key="explodingKittens.state.peekPending",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                card=card,
                extra={"state": "see_the_future", "preview_card": card},
            )

        if bool(team.get("pending_attack")) and not target_team_id:
            return ExplodingKittensResult(
                status="pending_state",
                message_key="explodingKittens.state.attackNeedsTarget",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                card=card,
                extra={"state": "attack"},
            )

        if bool(team.get("pending_attack")):
            if target_team_id is None:
                raise ValueError("explodingKittens.state.attackNeedsTarget")
            target_team = self._repository.getTeamByGameIdAndTeamId(db, game_id, target_team_id)
            if target_team is None:
                raise ValueError("explodingKittens.state.attackInvalidTarget")

            action_id = str(uuid4())
            self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(
                db,
                game_id,
                team_id,
                {"pending_attack": False},
            )
            self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                db,
                game_id,
                str(card.get("id") or ""),
                {"locked": True},
            )
            self._repository.createCardActionWithoutCommit(
                db,
                {
                    "id": action_id,
                    "game_id": game_id,
                    "source_team_id": team_id,
                    "target_team_id": target_team_id,
                    "card_id": str(card.get("id") or ""),
                    "action_type": _ACTION_ATTACK,
                    "status": _ACTION_PENDING,
                    "created_at": datetime.now(UTC).replace(tzinfo=None),
                    "resolved_at": None,
                    "context": None,
                },
            )
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.play.attackTargeted",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                card=card,
                extra={"state": "attack", "target_team_id": target_team_id, "action_id": action_id},
            )

        result_message = self._applyCardEffectWithoutCommit(db, game_id=game_id, team_id=team_id, card=card)
        self._repository.commitChanges(db)

        return ExplodingKittensResult(
            status="ok",
            message_key=result_message,
            state=self.getTeamState(db, game_id=game_id, team_id=team_id),
            card=card,
        )

    def resolveState(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        resolve_skip: bool,
        confirm_peek: bool,
        reject_peek: bool,
        qr_token: Optional[str],
        target_team_id: Optional[str],
    ) -> ExplodingKittensResult:
        team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
        if team is None:
            raise ValueError("team.notFound")

        if bool(team.get("pending_skip")):
            if not resolve_skip:
                return ExplodingKittensResult(
                    status="pending_state",
                    message_key="explodingKittens.state.skipPending",
                    state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                    extra={"state": "skip"},
                )
            self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(db, game_id, team_id, {"pending_skip": False})
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.state.skipResolved",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                extra={"state": "skip"},
            )

        if bool(team.get("pending_peek")):
            if confirm_peek and reject_peek:
                raise ValueError("explodingKittens.state.peekNeedsConfirm")
            if not qr_token:
                return ExplodingKittensResult(
                    status="pending_state",
                    message_key="explodingKittens.state.peekNeedsConfirm",
                    state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                    extra={"state": "see_the_future"},
                )
            if reject_peek:
                self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(db, game_id, team_id, {"pending_peek": False})
                self._repository.commitChanges(db)
                return ExplodingKittensResult(
                    status="ok",
                    message_key="explodingKittens.state.peekResolved",
                    state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                    extra={"state": "see_the_future", "rejected": True},
                )
            if not confirm_peek:
                return ExplodingKittensResult(
                    status="pending_state",
                    message_key="explodingKittens.state.peekNeedsConfirm",
                    state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                    extra={"state": "see_the_future"},
                )

            self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(db, game_id, team_id, {"pending_peek": False})
            self._repository.commitChanges(db)
            return self.scanCard(
                db,
                game_id=game_id,
                team_id=team_id,
                qr_token=qr_token,
                target_team_id=target_team_id,
            )

        if bool(team.get("pending_attack")):
            if not qr_token or not target_team_id:
                return ExplodingKittensResult(
                    status="pending_state",
                    message_key="explodingKittens.state.attackNeedsTarget",
                    state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                    extra={"state": "attack"},
                )
            return self.scanCard(
                db,
                game_id=game_id,
                team_id=team_id,
                qr_token=qr_token,
                target_team_id=target_team_id,
            )

        return ExplodingKittensResult(
            status="ok",
            message_key="explodingKittens.state.none",
            state=self.getTeamState(db, game_id=game_id, team_id=team_id),
        )

    def useCombo(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        card_ids: list[str],
        target_team_id: Optional[str],
        requested_card_type: Optional[str],
    ) -> ExplodingKittensResult:
        if len(card_ids) < 2:
            raise ValueError("explodingKittens.combo.invalid")

        hand_cards = self._repository.fetchHandCardsByTeamId(db, team_id)
        hand_by_id = {str(card["id"]): card for card in hand_cards}

        cards: list[Dict[str, Any]] = []
        for card_id in card_ids:
            card = hand_by_id.get(card_id)
            if card is None:
                raise ValueError("explodingKittens.combo.cardNotInHand")
            cards.append(card)

        counts: Dict[str, int] = {}
        for card in cards:
            type_value = str(card.get("type") or "")
            counts[type_value] = counts.get(type_value, 0) + 1

        count = len(cards)
        if count == 2:
            if len(counts.keys()) != 1:
                raise ValueError("explodingKittens.combo.invalid")
            action_id = self._queueComboTwoSame(db, game_id=game_id, team_id=team_id, target_team_id=target_team_id)
            for card in cards:
                self._repository.updateCardByGameIdAndCardIdWithoutCommit(db, game_id, str(card["id"]), {"holder_team_id": None})
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.combo.twoReady",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                extra={"combo_type": "two_same", "action_id": action_id, "target_team_id": target_team_id},
            )

        if count == 3:
            if len(counts.keys()) != 1:
                raise ValueError("explodingKittens.combo.invalid")
            if not requested_card_type:
                raise ValueError("explodingKittens.combo.threeNeedsType")
            action_id = self._queueComboThreeSame(
                db,
                game_id=game_id,
                team_id=team_id,
                target_team_id=target_team_id,
                requested_card_type=requested_card_type,
            )
            for card in cards:
                self._repository.updateCardByGameIdAndCardIdWithoutCommit(db, game_id, str(card["id"]), {"holder_team_id": None})
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.combo.threeReady",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                extra={"combo_type": "three_same", "action_id": action_id, "target_team_id": target_team_id},
            )

        if count == 5:
            if len(counts.keys()) != 5:
                raise ValueError("explodingKittens.combo.invalid")
            if not requested_card_type:
                raise ValueError("explodingKittens.combo.fiveNeedsType")
            for card in cards:
                self._repository.updateCardByGameIdAndCardIdWithoutCommit(db, game_id, str(card["id"]), {"holder_team_id": None})

            deck_card = self._repository.fetchFirstAvailableCardByGameIdAndType(db, game_id, requested_card_type)
            if deck_card is not None:
                self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                    db,
                    game_id,
                    str(deck_card["id"]),
                    {"holder_team_id": team_id},
                )
                self._repository.commitChanges(db)
                return ExplodingKittensResult(
                    status="ok",
                    message_key="explodingKittens.combo.fiveTaken",
                    state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                    extra={"combo_type": "five_different", "card_type": requested_card_type},
                )

            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.combo.fiveNone",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
                extra={"combo_type": "five_different"},
            )

        raise ValueError("explodingKittens.combo.invalid")

    def resolveAction(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        action_id: str,
        use_nope: bool,
    ) -> ExplodingKittensResult:
        action = self._repository.getPendingActionByIdForTeam(
            db,
            action_id=action_id,
            game_id=game_id,
            target_team_id=team_id,
        )
        if action is None:
            raise ValueError("explodingKittens.action.notAllowed")

        if use_nope:
            nope_card = self._repository.fetchFirstHandCardByTeamIdAndType(db, team_id, "nope")
            if nope_card is None:
                raise ValueError("explodingKittens.action.nopeMissing")

            self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                db,
                game_id,
                str(nope_card["id"]),
                {"holder_team_id": None},
            )
            self._repository.updateActionStatusWithoutCommit(db, action_id, _ACTION_CANCELED)
            card_id = action.get("card_id")
            if card_id:
                self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                    db,
                    game_id,
                    str(card_id),
                    {"locked": False},
                )
            self._repository.commitChanges(db)
            return ExplodingKittensResult(
                status="ok",
                message_key="explodingKittens.action.nopeUsed",
                state=self.getTeamState(db, game_id=game_id, team_id=team_id),
            )

        action_type = str(action.get("action_type") or "")
        if action_type == _ACTION_FAVOR:
            result_key, extra = self._resolveFavorWithoutCommit(db, action)
        elif action_type == _ACTION_ATTACK:
            result_key, extra = self._resolveAttackWithoutCommit(db, game_id, action)
        elif action_type == _ACTION_COMBO_TWO_SAME:
            result_key, extra = self._resolveComboTwoSameWithoutCommit(db, action)
        elif action_type == _ACTION_COMBO_THREE_SAME:
            result_key, extra = self._resolveComboThreeSameWithoutCommit(db, action)
        else:
            raise ValueError("explodingKittens.action.invalid")

        self._repository.updateActionStatusWithoutCommit(db, action_id, _ACTION_RESOLVED)
        card_id = action.get("card_id")
        if card_id:
            self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                db,
                game_id,
                str(card_id),
                {"locked": False},
            )
        self._repository.commitChanges(db)

        return ExplodingKittensResult(
            status="ok",
            message_key=result_key,
            state=self.getTeamState(db, game_id=game_id, team_id=team_id),
            extra=extra,
        )

    def _resolveFavorWithoutCommit(self, db: DbSession, action: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        source_team_id = str(action.get("source_team_id") or "")
        target_team_id = str(action.get("target_team_id") or "")
        if not source_team_id or not target_team_id:
            raise ValueError("explodingKittens.action.invalid")

        cards = self._repository.fetchHandCardsByTeamId(db, target_team_id)
        if not cards:
            return "explodingKittens.action.favorNoCards", {}

        card = random.choice(cards)
        self._repository.updateCardByGameIdAndCardIdWithoutCommit(
            db,
            str(card.get("game_id")),
            str(card.get("id")),
            {"holder_team_id": source_team_id},
        )
        return "explodingKittens.action.favorTaken", {"card_type": str(card.get("type") or "")}

    def _resolveAttackWithoutCommit(self, db: DbSession, game_id: str, action: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        card_id = str(action.get("card_id") or "")
        target_team_id = str(action.get("target_team_id") or "")
        if not card_id or not target_team_id:
            raise ValueError("explodingKittens.action.invalid")

        card = self._repository.getCardByGameIdAndCardId(db, game_id, card_id)
        if card is None:
            raise ValueError("explodingKittens.cards.notFound")
        message = self._applyCardEffectWithoutCommit(db, game_id=game_id, team_id=target_team_id, card=card)
        return message, {}

    def _resolveComboTwoSameWithoutCommit(self, db: DbSession, action: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        source_team_id = str(action.get("source_team_id") or "")
        target_team_id = str(action.get("target_team_id") or "")
        if not source_team_id or not target_team_id:
            raise ValueError("explodingKittens.action.invalid")

        cards = self._repository.fetchHandCardsByTeamId(db, target_team_id)
        if not cards:
            return "explodingKittens.combo.twoNoCards", {}

        card = random.choice(cards)
        self._repository.updateCardByGameIdAndCardIdWithoutCommit(
            db,
            str(card.get("game_id")),
            str(card.get("id")),
            {"holder_team_id": source_team_id},
        )
        return "explodingKittens.combo.twoTaken", {"card_type": str(card.get("type") or "")}

    def _resolveComboThreeSameWithoutCommit(self, db: DbSession, action: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        source_team_id = str(action.get("source_team_id") or "")
        target_team_id = str(action.get("target_team_id") or "")
        if not source_team_id or not target_team_id:
            raise ValueError("explodingKittens.action.invalid")

        context_raw = action.get("context")
        context: Dict[str, Any] = {}
        if isinstance(context_raw, dict):
            context = context_raw
        elif isinstance(context_raw, str) and context_raw:
            try:
                parsed = json.loads(context_raw)
                if isinstance(parsed, dict):
                    context = parsed
            except json.JSONDecodeError:
                context = {}

        requested_type = str(context.get("requestedCardType") or "")
        if requested_type not in _VALID_CARD_TYPES:
            raise ValueError("explodingKittens.combo.threeInvalidType")

        cards = self._repository.fetchHandCardsByTeamId(db, target_team_id)
        for card in cards:
            if str(card.get("type") or "") == requested_type:
                self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                    db,
                    str(card.get("game_id")),
                    str(card.get("id")),
                    {"holder_team_id": source_team_id},
                )
                return "explodingKittens.combo.threeTaken", {"card_type": requested_type}

        return "explodingKittens.combo.threeNoMatch", {}

    def _queueComboTwoSame(self, db: DbSession, *, game_id: str, team_id: str, target_team_id: Optional[str]) -> str:
        if not target_team_id:
            raise ValueError("explodingKittens.combo.needsTarget")
        if target_team_id == team_id:
            raise ValueError("explodingKittens.combo.targetSelf")
        target = self._repository.getTeamByGameIdAndTeamId(db, game_id, target_team_id)
        if target is None:
            raise ValueError("explodingKittens.combo.targetInvalid")

        action_id = str(uuid4())
        self._repository.createCardActionWithoutCommit(
            db,
            {
            "id": action_id,
                "game_id": game_id,
                "source_team_id": team_id,
                "target_team_id": target_team_id,
                "card_id": None,
                "action_type": _ACTION_COMBO_TWO_SAME,
                "status": _ACTION_PENDING,
                "created_at": datetime.now(UTC).replace(tzinfo=None),
                "resolved_at": None,
                "context": None,
            },
        )
        return action_id

    def _queueComboThreeSame(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        target_team_id: Optional[str],
        requested_card_type: str,
    ) -> str:
        if not target_team_id:
            raise ValueError("explodingKittens.combo.needsTarget")
        if target_team_id == team_id:
            raise ValueError("explodingKittens.combo.targetSelf")
        target = self._repository.getTeamByGameIdAndTeamId(db, game_id, target_team_id)
        if target is None:
            raise ValueError("explodingKittens.combo.targetInvalid")
        if requested_card_type not in _VALID_CARD_TYPES:
            raise ValueError("explodingKittens.combo.threeInvalidType")

        action_id = str(uuid4())
        self._repository.createCardActionWithoutCommit(
            db,
            {
            "id": action_id,
                "game_id": game_id,
                "source_team_id": team_id,
                "target_team_id": target_team_id,
                "card_id": None,
                "action_type": _ACTION_COMBO_THREE_SAME,
                "status": _ACTION_PENDING,
                "created_at": datetime.now(UTC).replace(tzinfo=None),
                "resolved_at": None,
                "context": json.dumps({"requestedCardType": requested_card_type}),
            },
        )
        return action_id

    def _applyCardEffectWithoutCommit(self, db: DbSession, *, game_id: str, team_id: str, card: Dict[str, Any]) -> str:
        card_type = str(card.get("type") or "")
        card_id = str(card.get("id"))

        if card_type == "exploding_kitten":
            defuse = self._repository.fetchFirstHandCardByTeamIdAndType(db, team_id, "defuse")
            if defuse is not None:
                self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                    db,
                    game_id,
                    str(defuse["id"]),
                    {"holder_team_id": None},
                )
                self._repository.createCardUsageWithoutCommit(
                    db,
                    usage_id=str(uuid4()),
                    card_id=str(defuse["id"]),
                    team_id=team_id,
                    event_type="defuse_used",
                )
                return "explodingKittens.scan.explodingDefused"

            team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            if team is None:
                raise ValueError("team.notFound")
            lives = int(team.get("lives") or 0)
            self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(
                db,
                game_id,
                team_id,
                {"lives": max(0, lives - 1)},
            )
            self._repository.createCardUsageWithoutCommit(
                db,
                usage_id=str(uuid4()),
                card_id=card_id,
                team_id=team_id,
                event_type="exploding_hit",
            )
            return "explodingKittens.scan.explodingHit"

        if card_type == "felix":
            if self._repository.wasCardUsedByTeamForEvent(db, card_id, team_id, "felix"):
                return "explodingKittens.scan.felixAlreadyUsed"

            team = self._repository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            if team is None:
                raise ValueError("team.notFound")
            lives = int(team.get("lives") or 0)
            self._repository.updateTeamByGameIdAndTeamIdWithoutCommit(
                db,
                game_id,
                team_id,
                {"lives": lives + 1},
            )
            self._repository.createCardUsageWithoutCommit(
                db,
                usage_id=str(uuid4()),
                card_id=card_id,
                team_id=team_id,
                event_type="felix",
            )
            return "explodingKittens.scan.felixGain"

        if card_type in _SCANNABLE_TO_HAND:
            self._repository.updateCardByGameIdAndCardIdWithoutCommit(
                db,
                game_id,
                card_id,
                {"holder_team_id": team_id},
            )
            return "explodingKittens.scan.cardAdded"

        return "explodingKittens.scan.cardNoEffect"
