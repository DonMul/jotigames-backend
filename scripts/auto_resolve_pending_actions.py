from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.version_info < (3, 11):
    raise RuntimeError("auto_resolve_pending_actions.py requires Python 3.11+. Use the workspace venv: ../.venv/bin/python")

from app.database import SessionLocal
from app.modules.exploding_kittens import ExplodingKittensModule
from app.repositories.exploding_kittens_repository import ExplodingKittensRepository
from app.services.exploding_kittens_service import ExplodingKittensService
from app.services.ws_client import WsEventPublisher


def resolve_single_stale_action(
    module: ExplodingKittensModule,
    repository: ExplodingKittensRepository,
    service: ExplodingKittensService,
) -> bool:
    with SessionLocal() as db:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
        pending_action = repository.fetchOldestPendingActionOlderThan(db, older_than=cutoff)
        if pending_action is None:
            return False

        game_id = str(pending_action.get("game_id") or "").strip()
        target_team_id = str(pending_action.get("target_team_id") or "").strip()
        source_team_id = str(pending_action.get("source_team_id") or "").strip()
        action_id = str(pending_action.get("id") or "").strip()
        if not game_id or not target_team_id or not action_id:
            return False

        target_team_before = repository.getTeamByGameIdAndTeamId(db, game_id, target_team_id)
        target_hand_before = repository.fetchHandCardsByTeamId(db, target_team_id)

        source_team_before = None
        source_hand_before: list[dict] = []
        if source_team_id and source_team_id != target_team_id:
            source_team_before = repository.getTeamByGameIdAndTeamId(db, game_id, source_team_id)
            source_hand_before = repository.fetchHandCardsByTeamId(db, source_team_id)

        try:
            service.resolveAction(
                db,
                game_id=game_id,
                team_id=target_team_id,
                action_id=action_id,
                use_nope=False,
            )
        except Exception as error:  # noqa: BLE001
            repository.rollbackOnError(db, error)
            print(f"[auto-resolve] failed to resolve action {action_id}: {error}")
            return False

        target_team_after = repository.getTeamByGameIdAndTeamId(db, game_id, target_team_id)
        target_hand_after = repository.fetchHandCardsByTeamId(db, target_team_id)
        module._publish_team_hand_diff_events(
            game_id=game_id,
            team_id=target_team_id,
            before_cards=target_hand_before,
            after_cards=target_hand_after,
        )
        module._publish_lives_transition_event(
            game_id=game_id,
            team_id=target_team_id,
            before=target_team_before,
            after=target_team_after,
        )

        if source_team_id and source_team_before is not None:
            source_team_after = repository.getTeamByGameIdAndTeamId(db, game_id, source_team_id)
            source_hand_after = repository.fetchHandCardsByTeamId(db, source_team_id)
            module._publish_team_hand_diff_events(
                game_id=game_id,
                team_id=source_team_id,
                before_cards=source_hand_before,
                after_cards=source_hand_after,
            )
            module._publish_lives_transition_event(
                game_id=game_id,
                team_id=source_team_id,
                before=source_team_before,
                after=source_team_after,
            )
        else:
            source_team_after = source_team_before
            source_hand_after = source_hand_before

        module._publish_action_removed_events(
            game_id=game_id,
            action_id=action_id,
            target_team_id=target_team_id,
            status_value="resolved",
        )

        module._publish_action_resolution_messages(
            game_id=game_id,
            action=pending_action,
            use_nope=False,
            target_team_before=target_team_before,
            target_team_after=target_team_after,
            source_team_before=source_team_before,
            source_team_after=source_team_after,
            target_hand_before=target_hand_before,
            target_hand_after=target_hand_after,
            source_hand_before=source_hand_before,
            source_hand_after=source_hand_after,
        )

        print(f"[auto-resolve] resolved stale action {action_id} for team {target_team_id}")
        return True


def main() -> None:
    module = ExplodingKittensModule(WsEventPublisher())
    repository = ExplodingKittensRepository()
    service = ExplodingKittensService()

    print("[auto-resolve] starting stale action resolver loop")
    while True:
        resolved = resolve_single_stale_action(module, repository, service)
        if not resolved:
            time.sleep(1)


if __name__ == "__main__":
    main()
