from app.dependencies import DbSession
from app.repositories.crazy88_repository import Crazy88Repository
from app.services.game_logic_service import GameActionResult, GameLogicService


class Crazy88Service(GameLogicService):
    def __init__(self) -> None:
        super().__init__("crazy_88", repository=Crazy88Repository())

    def submit_task(self, db: DbSession, *, game_id: str, team_id: str, task_id: str) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="crazy88.task.submit",
            object_id=task_id,
            points_awarded=0,
            allow_repeat=True,
            success_message_key="crazy88.task.submitted",
            already_message_key="crazy88.task.submitted",
        )

    def judge_submission(self, db: DbSession, *, game_id: str, team_id: str, submission_id: str, accepted: bool) -> GameActionResult:
        points = 1 if accepted else 0
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="crazy88.review.judge",
            object_id=submission_id,
            points_awarded=points,
            allow_repeat=False,
            metadata={"accepted": bool(accepted)},
            success_message_key="crazy88.review.judged",
            already_message_key="crazy88.review.alreadyJudged",
        )
