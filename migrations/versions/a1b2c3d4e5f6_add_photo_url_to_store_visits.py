"""add photo_url to store_visits

Revision ID: a1b2c3d4e5f6
Revises: 763568154d36
Create Date: 2026-03-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '763568154d36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('store_visits', sa.Column('photo_url', sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column('store_visits', 'photo_url')
