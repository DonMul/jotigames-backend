import secrets
from datetime import UTC, datetime
from uuid import uuid4

from app.dependencies import DbSession
from app.repositories.card_repository import CardRepository

_EXPLODING_KITTENS_CARD_COUNTS = [
    ("attack", 10),
    ("defuse", 10),
    ("exploding_kitten", 10),
    ("favor", 10),
    ("felix", 10),
    ("nope", 10),
    ("see_the_future", 10),
    ("shuffle", 10),
    ("skip", 10),
    ("random1", 5),
    ("random2", 5),
    ("random3", 5),
    ("random4", 5),
    ("random5", 5),
]


class GameInitializerService:
    def __init__(self) -> None:
        """Initialize game bootstrap service dependencies."""
        self._cardRepository = CardRepository()

    def initializeGameByIdAndType(self, db: DbSession, game_id: str, game_type: str) -> None:
        """Run post-create bootstrap for game-type specific seed data.

        This hook is called immediately after game creation and before commit so
        game-specific initial entities can be inserted transactionally.
        """
        if game_type == "exploding_kittens":
            self._initializeExplodingKittens(db, game_id)

    def _initializeExplodingKittens(self, db: DbSession, game_id: str) -> None:
        """Seed Exploding Kittens cards with deterministic card-type distribution."""
        created_at = datetime.now(UTC).replace(tzinfo=None)
        cards = []

        for card_type, count in _EXPLODING_KITTENS_CARD_COUNTS:
            for _ in range(count):
                cards.append(
                    self._cardRepository.buildCardValues(
                        card_id=str(uuid4()),
                        game_id=game_id,
                        card_type=card_type,
                        qr_token=secrets.token_hex(16),
                        created_at=created_at,
                        title=f"card.type.{card_type}",
                    )
                )

        self._cardRepository.createCardsByValuesWithoutCommit(db, cards)
