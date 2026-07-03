"""add deleted_reason to routes and stores

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-05-05 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('routes', sa.Column('deleted_reason', sa.String(500), nullable=True))
    op.add_column('stores', sa.Column('deleted_reason', sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column('stores', 'deleted_reason')
    op.drop_column('routes', 'deleted_reason')
