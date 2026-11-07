"""panel web: códigos de acceso y sesiones (RF-PAN-001, RNF-SEG-004)

Revision ID: 9a1c5e7d2b40
Revises: 3208b0452a58
Create Date: 2026-11-07 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9a1c5e7d2b40'
down_revision: Union[str, Sequence[str], None] = '3208b0452a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('codigos_acceso',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('staff_id', sa.Integer(), nullable=False),
    sa.Column('codigo_hash', sa.String(length=64), nullable=False),
    sa.Column('expira_en', sa.DateTime(timezone=True), nullable=False),
    sa.Column('intentos', sa.Integer(), nullable=False),
    sa.Column('usado', sa.Boolean(), nullable=False),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_codigos_acceso_staff_id'), 'codigos_acceso', ['staff_id'], unique=False)
    op.create_table('sesiones_panel',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('staff_id', sa.Integer(), nullable=False),
    sa.Column('expira_en', sa.DateTime(timezone=True), nullable=False),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ultimo_uso_en', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_index(op.f('ix_sesiones_panel_staff_id'), 'sesiones_panel', ['staff_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_sesiones_panel_staff_id'), table_name='sesiones_panel')
    op.drop_table('sesiones_panel')
    op.drop_index(op.f('ix_codigos_acceso_staff_id'), table_name='codigos_acceso')
    op.drop_table('codigos_acceso')
