"""ontology: entities, annotations, scene_entities

Revision ID: ontology
Revises: screenplays_scenes
Create Date: 2026-08-29 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision: str = 'ontology'
down_revision: str | Sequence[str] | None = 'screenplays_scenes'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create entity_type enum (_create_events=False so create_table doesn't re-emit it)
    entity_type_enum = sa.Enum(
        'character', 'location', 'prop', 'vehicle', 'set_dressing',
        'wardrobe', 'sound', 'vfx', 'sfx', 'makeup', 'hair', 'stunt',
        'animal', 'extra', 'equipment', 'camera_setup', 'other',
        name='entity_type', _create_events=False
    )
    entity_type_enum.create(op.get_bind(), checkfirst=True)

    # entities table
    op.create_table(
        'entities',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('project_id', UUID(as_uuid=True), nullable=False),
        sa.Column('entity_type', entity_type_enum, nullable=False),
        sa.Column('canonical_name', sa.String(length=255), nullable=False),
        sa.Column('aliases', ARRAY(sa.String), nullable=False, server_default='{}'),
        sa.Column('attributes', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'entity_type', 'canonical_name', name='uq_entity_project_type_name')
    )
    op.create_index('entities_name_trgm', 'entities', ['canonical_name'], 
                    postgresql_using='gin', postgresql_ops={'canonical_name': 'gin_trgm_ops'})

    # annotations table
    op.create_table(
        'annotations',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('scene_id', UUID(as_uuid=True), nullable=False),
        sa.Column('node_id', sa.Text(), nullable=False),
        sa.Column('start_offset', sa.Integer(), nullable=False),
        sa.Column('end_offset', sa.Integer(), nullable=False),
        sa.Column('entity_id', UUID(as_uuid=True), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['scene_id'], ['scenes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("source in ('manual','ai_accepted')", name='ck_annotations_source')
    )
    op.create_index('annotations_scene', 'annotations', ['scene_id'])
    op.create_index('annotations_entity', 'annotations', ['entity_id'])

    # scene_entities table
    op.create_table(
        'scene_entities',
        sa.Column('scene_id', UUID(as_uuid=True), nullable=False),
        sa.Column('entity_id', UUID(as_uuid=True), nullable=False),
        sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['scene_id'], ['scenes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('scene_id', 'entity_id')
    )


def downgrade() -> None:
    op.drop_table('scene_entities')
    op.drop_index('annotations_entity', table_name='annotations')
    op.drop_index('annotations_scene', table_name='annotations')
    op.drop_table('annotations')
    op.drop_index('entities_name_trgm', table_name='entities')
    op.drop_table('entities')

    entity_type_enum = sa.Enum(
        'character', 'location', 'prop', 'vehicle', 'set_dressing',
        'wardrobe', 'sound', 'vfx', 'sfx', 'makeup', 'hair', 'stunt',
        'animal', 'extra', 'equipment', 'camera_setup', 'other',
        name='entity_type'
    )
    entity_type_enum.drop(op.get_bind(), checkfirst=True)
