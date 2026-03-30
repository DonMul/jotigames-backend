"""reserved revision for topup payment methods

Revision ID: 20260330_0010
Revises: 20260330_0009
Create Date: 2026-03-30 00:00:00.000000
"""

from typing import Optional, Sequence, Union

revision: str = "20260330_0010"
down_revision: Optional[str] = "20260330_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Top-up payment methods are centralized in code (same as subscriptions).
    # Keep this revision as a no-op so existing revision chains remain valid.
    pass


def downgrade() -> None:
    pass
