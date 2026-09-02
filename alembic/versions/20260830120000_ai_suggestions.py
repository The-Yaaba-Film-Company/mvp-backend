"""ai_suggestions: AI extraction pipeline

Revision ID: ai_suggestions
Revises: scene_metrics
Create Date: 2026-08-30 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = 'ai_suggestions'
down_revision: str | Sequence[str] | None = 'scene_metrics'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column(
        'scenes',
        sa.Column('last_ai_hash', sa.Text(), nullable=True),
    )

    entity_type_enum = sa.Enum(
        'character', 'location', 'prop', 'vehicle', 'set_dressing',
        'wardrobe', 'sound', 'vfx', 'sfx', 'makeup', 'hair', 'stunt',
        'animal', 'extra', 'equipment', 'camera_setup', 'other',
        name='entity_type', _create_events=False
    )

    op.create_table(
        'ai_suggestions',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('scene_id', UUID(as_uuid=True), nullable=False),
        sa.Column('node_id', sa.Text(), nullable=False),
        sa.Column('matched_text', sa.Text(), nullable=False),
        sa.Column('start_offset', sa.Integer(), nullable=True),
        sa.Column('end_offset', sa.Integer(), nullable=True),
        sa.Column('suggested_type', entity_type_enum, nullable=False),
        sa.Column('suggested_name', sa.Text(), nullable=False),
        sa.Column('matched_entity_id', UUID(as_uuid=True), nullable=True),
        sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('prompt_version', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('reviewed_by', UUID(as_uuid=True), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['scene_id'], ['scenes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['matched_entity_id'], ['entities.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("status in ('pending','accepted','rejected')", name='ck_ai_suggestions_status')
    )
    op.create_index(
        'ai_suggestions_scene', 'ai_suggestions', ['scene_id'],
        postgresql_where=sa.text("status = 'pending'")
    )


def downgrade() -> None:
    op.drop_index('ai_suggestions_scene', table_name='ai_suggestions')
    op.drop_table('ai_suggestions')
    op.drop_column('scenes', 'last_ai_hash')