"""Persist remote snapshots, local drafts and archived offer bindings."""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("streams", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False), sa.Column("name", sa.String(255), nullable=False),
        sa.Column("remote", sa.JSON(), nullable=False), sa.Column("published", sa.JSON(), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=False), sa.Column("version", sa.Integer(), nullable=False))
    op.create_index("ix_streams_campaign_id", "streams", ["campaign_id"])


def downgrade():
    op.drop_index("ix_streams_campaign_id", table_name="streams")
    op.drop_table("streams")
