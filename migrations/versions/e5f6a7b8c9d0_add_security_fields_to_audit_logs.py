"""add security fields to audit_logs

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
Create Date: 2026-05-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('audit_logs', sa.Column('ip_address', sa.String(45), nullable=True))
    op.add_column('audit_logs', sa.Column('details', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('audit_logs', 'details')
    op.drop_column('audit_logs', 'ip_address')
