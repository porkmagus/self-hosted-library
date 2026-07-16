# ADR 0002: PostgreSQL-led, fenced, resumable ingestion

- Status: Accepted
- Date: 2026-07-16

## Context

Celery delivery is at-least-once, workers may die or lose leases, and PostgreSQL, S3-compatible storage, and Qdrant cannot participate in one distributed transaction. A source must remain recoverable across process and container replacement without exposing partial or stale vector generations.

## Decision

PostgreSQL is the ingestion control plane.

- The API first commits an `uploading` book reservation, streams the private source through the API into S3-compatible storage, verifies its size, then atomically commits source metadata, a `pending` ingestion job, and an outbox event.
- A periodic stale-reservation collector removes objects left by crashes between source upload and job acceptance.
- Celery receives only a durable job UUID. PostgreSQL stores lifecycle state, processing stage, attempts, artifacts, checkpoints, and error details.
- A claim receives a random lease token and monotonically increasing claim epoch. Every worker mutation requires UUID, generation, token, epoch, running state, and an unexpired lease. Database time is authoritative.
- Lifecycle (`pending`, `running`, `retry_wait`, terminal/cancellation states) is separate from processing stage.
- Extracted text and versioned canonical text/image manifests are immutable S3 artifacts with SHA-256 checksums. Fragment paths and private generation-scoped PDF image objects are persisted before vector writes.
- Qdrant point IDs derive from a fixed application namespace plus book UUID, generation, manifest SHA-256, logical item identity, and claim epoch. A synchronous `wait=true` upsert completes before its fenced PostgreSQL checkpoint records the accepted logical range and owning epoch in one transaction. A checkpointed range is never resubmitted by that claim.
- Workers write `visible=false` to both text and CLIP image collections. Successful processing atomically records `succeeded` and emits an activation outbox event. A serialized reconciler derives every accepted point ID from the immutable manifests and committed range ownership, retrieves those exact IDs with vectors, verifies their full payload identity, and stamps only that set with a deterministic activation token.
- PostgreSQL records the active `(generation, job UUID, activation token)` only after both text and image sets are stamped. Qdrant queries prefilter by PostgreSQL's active tokens before bounded top-N retrieval, then results and cached payloads are reauthorized against PostgreSQL. This prevents uncommitted or stale visible points from being returned or crowding valid points out of top-N. Image results use application-mediated API stream URLs rather than object-store URLs.
- After the PostgreSQL activation commit, durable cleanup under the same per-book advisory lock hides noncurrent tokens. A pending-cleanup flag lets reconciliation recover a crash at this boundary without allowing old cleanup to hide a newer activation.
- Book cancellation increments the processing generation and invalidates active worker fences before asynchronous object/vector cleanup.
- PostgreSQL-to-Celery publication uses a transactional outbox with retry and stale-publisher reclamation. Duplicate delivery is expected and safe.

## Lock ordering

Transactions that mutate both aggregates lock the book before the ingestion job. Generation activation and cleanup additionally use a per-book PostgreSQL advisory transaction lock while reconciling external state.

## Consequences

- Worker death resumes from the next committed canonical chunk/image batch. Different claims use disjoint point IDs; activation may safely union ranges accepted from multiple claims.
- An uncheckpointed late write remains hidden and cannot overwrite or join the accepted set.
- Expired workers may leave hidden garbage but cannot expose it or advance PostgreSQL state; cleanup is idempotent.
- The prior token remains queryable until PostgreSQL commits the replacement token, so external promotion does not create an intentional empty or mixed-generation window.
- Legacy Qdrant points missing the `visible` payload are backfilled to visible during collection initialization.
