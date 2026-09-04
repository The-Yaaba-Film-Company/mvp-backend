"""scene node_order

Revision ID: scene_node_order
Revises: d4cdb1784a81
Create Date: 2026-09-03 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'scene_node_order'
down_revision: str | Sequence[str] | None = 'd4cdb1784a81'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'scenes',
        sa.Column(
            'node_order',
            sa.ARRAY(sa.String()),
            nullable=False,
            server_default='{}',
        ),
    )
    # Existing rows default to an empty order array; the backend treats an
    # empty node_order as "no canonical order yet" and adopts the next saved
    # content's order, so no backfill is required.


def downgrade() -> None:
    op.drop_column('scenes', 'node_order')
