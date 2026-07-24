"""Retry failed ingestion jobs by resetting them to pending.

Usage (inside grimoire-app-1 container):
    python retry_failed.py [--dry-run] [batch_limit]

Resets eligible failed ingestion jobs and their books back to pending so the
beat scheduler re-queues them. Also clears corresponding outbox entries.
"""
import sys

from sqlalchemy import create_engine, text

from api.config import settings

engine = create_engine(settings.DATABASE_URL)

dry_run = "--dry-run" in sys.argv
batch_limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0

with engine.begin() as conn:
    result = conn.execute(text("""
        SELECT COUNT(*) FROM ingestion_jobs j
        JOIN books b ON j.book_id = b.id
        WHERE j.state = 'failed'
          AND b.status = 'failed'
          AND b.deleted_at IS NULL
          AND j.generation = b.processing_generation
    """))
    total = result.scalar() or 0
    print(f"Total eligible failed jobs: {total}")

    limit = total if batch_limit <= 0 else min(batch_limit, total)
    if limit < total:
        print(f"Limiting reset to first {limit} jobs")

    if dry_run:
        print("DRY RUN — no changes made")
        sys.exit(0)

    if limit == 0:
        print("No eligible jobs to reset")
        sys.exit(0)

    reset_sql = text("""
    WITH eligible AS (
        SELECT j.id, j.book_id, j.uuid AS job_uuid, j.generation
        FROM ingestion_jobs j
        JOIN books b ON j.book_id = b.id
        WHERE j.state = 'failed'
          AND b.status = 'failed'
          AND b.deleted_at IS NULL
          AND j.generation = b.processing_generation
        ORDER BY j.id
        LIMIT :limit
    ),
    reset_jobs AS (
        UPDATE ingestion_jobs SET
            state = 'pending',
            stage = 'source_ready',
            attempt_count = 0,
            error_code = NULL,
            error_message = NULL,
            last_error_class = NULL,
            started_at = NULL,
            finished_at = NULL,
            retry_at = NULL,
            lease_owner = NULL,
            lease_token = NULL,
            lease_expires_at = NULL,
            progress_pct = 0
        FROM eligible
        WHERE ingestion_jobs.id = eligible.id
        RETURNING ingestion_jobs.id, eligible.book_id, eligible.job_uuid, eligible.generation
    ),
    reset_books AS (
        UPDATE books SET
            status = 'pending',
            error_message = NULL,
            total_chunks = 0,
            indexed_chunks = 0,
            total_images = 0,
            indexed_images = 0,
            indexed_generation = NULL,
            indexed_embedding_signature = NULL,
            indexed_job_uuid = NULL,
            indexed_activation_token = NULL,
            updated_at = NOW(),
            row_version = row_version + 1
        FROM reset_jobs
        WHERE books.id = reset_jobs.book_id
    ),
    reset_outbox AS (
        UPDATE ingestion_outbox SET
            state = 'pending',
            attempts = 0,
            claimed_at = NULL,
            published_at = NULL,
            publish_claim_token = NULL,
            last_error = NULL,
            last_recovered_claim_epoch = NULL,
            lifecycle_recovery_count = 0,
            available_at = NOW()
        FROM reset_jobs
        WHERE ingestion_outbox.job_uuid = reset_jobs.job_uuid
    )
    SELECT COUNT(*) FROM reset_jobs
    """)

    result = conn.execute(reset_sql, {"limit": limit})
    reset_count = result.scalar() or 0
    print(f"Reset {reset_count} jobs to pending")
    print("They will be picked up by the beat scheduler and re-queued")
