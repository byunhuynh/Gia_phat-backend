"""add vehicle table and route vehicle_id

Revision ID: e1f2g3h4i5j6
Revises: d1e2f3a4b5c6
Create Date: 2026-07-18 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2g3h4i5j6'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vehicles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plate_number', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plate_number')
    )
    op.add_column('routes', sa.Column('vehicle_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_routes_vehicle_id', 'routes', 'vehicles', ['vehicle_id'], ['id'])

    # Move existing plate values into the normalized vehicle table.
    op.execute(sa.text("""
        INSERT INTO vehicles (plate_number, created_at)
        SELECT DISTINCT UPPER(TRIM(vehicle_plate)), CURRENT_TIMESTAMP
        FROM routes
        WHERE vehicle_plate IS NOT NULL AND TRIM(vehicle_plate) <> ''
    """))
    op.execute(sa.text("""
        UPDATE routes
        SET vehicle_id = vehicles.id
        FROM vehicles
        WHERE vehicles.plate_number = UPPER(TRIM(routes.vehicle_plate))
    """))
    op.drop_column('routes', 'vehicle_plate')


def downgrade() -> None:
    op.add_column('routes', sa.Column('vehicle_plate', sa.String(length=20), nullable=True))
    op.execute(sa.text("""
        UPDATE routes
        SET vehicle_plate = vehicles.plate_number
        FROM vehicles
        WHERE routes.vehicle_id = vehicles.id
    """))
    op.drop_constraint('fk_routes_vehicle_id', 'routes', type_='foreignkey')
    op.drop_column('routes', 'vehicle_id')
    op.drop_table('vehicles')
