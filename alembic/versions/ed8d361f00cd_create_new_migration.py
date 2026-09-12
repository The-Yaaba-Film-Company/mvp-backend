"""create new  migration

Revision ID: ed8d361f00cd
Revises: 51adaba1de50
Create Date: 2026-09-09 21:57:40.028830

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ed8d361f00cd'
down_revision: Union[str, Sequence[str], None] = '51adaba1de50'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
