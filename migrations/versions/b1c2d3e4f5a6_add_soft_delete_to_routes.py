"""add soft delete to routes

Revision ID: b1c2d3e4f5a6
Revises: f7a3b1c9d2e8
Create Date: 2026-05-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'f7a3b1c9d2e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('routes', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('routes', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('routes', sa.Column('deleted_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
    op.create_index('idx_routes_is_deleted', 'routes', ['is_deleted'])


def downgrade() -> None:
    op.drop_index('idx_routes_is_deleted', table_name='routes')
    op.drop_column('routes', 'deleted_by')
    op.drop_column('routes', 'deleted_at')
    op.drop_column('routes', 'is_deleted')
