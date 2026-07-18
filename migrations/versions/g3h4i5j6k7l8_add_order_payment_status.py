"""add order payment status

Revision ID: g3h4i5j6k7l8
Revises: f2g3h4i5j6k7
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "g3h4i5j6k7l8"
down_revision: Union[str, Sequence[str], None] = "f2g3h4i5j6k7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sales_orders",
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("sales_orders", "is_paid")
