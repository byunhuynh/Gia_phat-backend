"""add owner to stores

Revision ID: f2g3h4i5j6k7
Revises: e1f2g3h4i5j6
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "f2g3h4i5j6k7"
down_revision: Union[str, Sequence[str], None] = "e1f2g3h4i5j6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.execute(sa.text("""
        UPDATE stores SET owner_id = routes.user_id
        FROM routes WHERE stores.route_id = routes.id
    """))
    op.alter_column("stores", "owner_id", nullable=False)
    op.create_foreign_key("fk_stores_owner_id", "stores", "users", ["owner_id"], ["id"])
    op.create_index("idx_stores_owner_id", "stores", ["owner_id"])


def downgrade() -> None:
    op.drop_index("idx_stores_owner_id", table_name="stores")
    op.drop_constraint("fk_stores_owner_id", "stores", type_="foreignkey")
    op.drop_column("stores", "owner_id")
