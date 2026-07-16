"""Add claim-scoped vector batch ownership and activation identity.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("indexed_activation_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "books",
        sa.Column("indexed_job_uuid", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "books",
        sa.Column(
            "activation_cleanup_pending",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "text_batch_claims",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "image_batch_claims",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "image_batch_claims")
    op.drop_column("ingestion_jobs", "text_batch_claims")
    op.drop_column("books", "activation_cleanup_pending")
    op.drop_column("books", "indexed_job_uuid")
    op.drop_column("books", "indexed_activation_token")
