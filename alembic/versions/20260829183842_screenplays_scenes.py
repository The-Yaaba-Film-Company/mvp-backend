"""screenplays_scenes

Revision ID: screenplays_scenes
Revises: 3fef7d54aeb8
Create Date: 2026-08-29 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = 'screenplays_scenes'
down_revision: str | Sequence[str] | None = '3fef7d54aeb8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create int_ext enum (_create_events=False so create_table doesn't re-emit it)
    int_ext_enum = sa.Enum('INT', 'EXT', 'INT_EXT', name='int_ext', _create_events=False)
    int_ext_enum.create(op.get_bind(), checkfirst=True)

    # screenplays table
    op.create_table(
        'screenplays',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('project_id', UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # scenes table
    op.create_table(
        'scenes',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('screenplay_id', UUID(as_uuid=True), nullable=False),
        sa.Column('order_key', sa.Float(), nullable=False),
        sa.Column('number', sa.Text(), nullable=True),
        sa.Column('number_suffix', sa.Text(), nullable=True),
        sa.Column('locked', sa.Boolean(), nullable=False, default=False),
        sa.Column('int_ext', int_ext_enum, nullable=True),
        sa.Column('location_entity_id', UUID(as_uuid=True), nullable=True),
        sa.Column('time_of_day', sa.Text(), nullable=True),
        sa.Column('heading_modifier', sa.Text(), nullable=True),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['screenplay_id'], ['screenplays.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('scenes_screenplay_order', 'scenes', ['screenplay_id', 'order_key'])


def downgrade() -> None:
    op.drop_index('scenes_screenplay_order', table_name='scenes')
    op.drop_table('scenes')
    op.drop_table('screenplays')
    
    # Drop int_ext enum
    int_ext_enum = sa.Enum('INT', 'EXT', 'INT_EXT', name='int_ext')
    int_ext_enum.drop(op.get_bind(), checkfirst=True)
