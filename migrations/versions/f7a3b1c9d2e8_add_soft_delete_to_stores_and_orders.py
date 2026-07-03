"""add soft delete to stores and sales_orders

Revision ID: f7a3b1c9d2e8
Revises: ca2008834448
Create Date: 2026-04-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7a3b1c9d2e8'
down_revision: Union[str, Sequence[str], None] = 'ca2008834448'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── stores ──────────────────────────────────────────────────────────────
    op.add_column('stores', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('stores', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('stores', sa.Column('deleted_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))

    op.create_index('idx_stores_is_deleted', 'stores', ['is_deleted'])

    # ── sales_orders ─────────────────────────────────────────────────────────
    op.add_column('sales_orders', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('sales_orders', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sales_orders', sa.Column('deleted_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))

    op.create_index('idx_salesorders_is_deleted', 'sales_orders', ['is_deleted'])


def downgrade() -> None:
    op.drop_index('idx_salesorders_is_deleted', table_name='sales_orders')
    op.drop_column('sales_orders', 'deleted_by')
    op.drop_column('sales_orders', 'deleted_at')
    op.drop_column('sales_orders', 'is_deleted')

    op.drop_index('idx_stores_is_deleted', table_name='stores')
    op.drop_column('stores', 'deleted_by')
    op.drop_column('stores', 'deleted_at')
    op.drop_column('stores', 'is_deleted')
