"""Fence stale dispatch recovery and record completed book cleanup.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("cleanup_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_outbox",
        sa.Column("last_recovered_claim_epoch", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "ingestion_outbox",
        sa.Column("publish_claim_token", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "ingestion_outbox",
        sa.Column(
            "lifecycle_recovery_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )



def downgrade() -> None:
    op.drop_column("ingestion_outbox", "lifecycle_recovery_count")
    op.drop_column("ingestion_outbox", "publish_claim_token")
    op.drop_column("ingestion_outbox", "last_recovered_claim_epoch")
    op.drop_column("books", "cleanup_completed_at")
