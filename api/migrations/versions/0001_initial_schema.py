"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-12

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("uuid", sa.String(36), unique=True, index=True, nullable=False),
        sa.Column("title", sa.String(512), nullable=False, index=True),
        sa.Column("author", sa.String(512), nullable=True),
        sa.Column("original_filename", sa.String(1024), nullable=False),
        sa.Column("sanitized_filename", sa.String(150), nullable=False),
        sa.Column("file_extension", sa.String(10), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("file_hash", sa.String(64), unique=True, index=True, nullable=True),
        sa.Column("minio_object_key", sa.String(1024), nullable=True),
        sa.Column("markdown_path", sa.String(1024), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "uploading", "extracting", "chunking",
                "embedding", "indexed", "failed",
                name="bookstatus",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexed_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("book_id", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(36), unique=True, index=True, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "uploading", "extracting", "chunking",
                "embedding", "indexed", "failed",
                name="bookstatus",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("ingestion_jobs")
    op.drop_table("books")
    sa.Enum(name="bookstatus").drop(op.get_bind(), checkfirst=True)
