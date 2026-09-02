"""scene_metrics

Revision ID: scene_metrics
Revises: ontology
Create Date: 2026-08-29 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = 'scene_metrics'
down_revision: str | Sequence[str] | None = 'ontology'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'scene_metrics',
        sa.Column('screenplay_id', UUID(as_uuid=True), nullable=False),
        sa.Column('scene_id', UUID(as_uuid=True), nullable=False),
        sa.Column('start_page', sa.Integer(), nullable=False),
        sa.Column('end_page', sa.Integer(), nullable=False),
        sa.Column('page_length', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['screenplay_id'], ['screenplays.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['scene_id'], ['scenes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('screenplay_id', 'scene_id')
    )


def downgrade() -> None:
    op.drop_table('scene_metrics')
