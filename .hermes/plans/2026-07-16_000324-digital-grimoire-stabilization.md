# Digital Grimoire Stabilization Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Recover the proven Digital Grimoire behavior, then evolve this repository into a reproducible, observable, restart-safe self-hosted RAG platform whose installer, ingest pipeline, semantic search, and image retrieval are tested end to end.

**Architecture:** Preserve the working FastAPI/React/PostgreSQL/Redis/Celery/Qdrant/Ollama shape. Put all object-store operations behind one neutral S3-compatible adapter, make PostgreSQL the durable ingest ledger, and make every expensive ingest stage idempotent and resumable. Infrastructure replacement is allowed only after black-box compatibility and recovery tests pass against the known-good baseline.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Celery/Redis, PostgreSQL, Qdrant, Ollama, PyMuPDF/Marker, CLIP, React/TypeScript/Vite, Docker Compose, GitHub Actions.

---

## Current state and risks

- `main` is at `7073724` with an uncommitted 11-file stabilization/storage experiment.
- The working tree currently mixes useful changes with an intentionally failing deployment test and an unfinished four-container SeaweedFS conversion.
- The README still describes MinIO/AIStor while Compose describes SeaweedFS.
- Browser upload creates an S3 object and a `Book`, then queues the object key as if it were a local path; that flow is broken by construction.
- `IngestionJob` exists but is unused. Celery task state and coarse `Book.indexed_chunks` are not sufficient restart checkpoints.
- A two-hour hard task limit was removed in WIP, but a fixed 12-hour Redis visibility timeout still risks duplicate execution for longer jobs.
- Qdrant IDs depend on batch-local ordering. Poison-chunk fallback can change point cardinality/order and leave stale points after retries.
- Local object keys are filename-derived and can collide.
- Image indexing catches broad exceptions, silently skips extraction failures, and returns direct object URLs that conflict with private object storage.
- Only one Alembic migration exists; schema evolution and upgrade/rollback have not been exercised.
- `upgrade.sh` backs up only configuration, not PostgreSQL/Qdrant/object data, and does not actually retain/restore a prior image reference.
- CI builds an image but does not run backend tests, frontend tests, static checks, Compose validation, migrations, or integration smoke tests.
- `doctor.sh`, `reset.sh`, README, setup files, service names, and health output have drifted apart.

## Non-negotiable acceptance criteria

1. A fresh supported Ubuntu host reaches a healthy UI with one documented setup command and no source-built infrastructure.
2. Setup is idempotent: rerunning it preserves data and configuration unless the operator explicitly requests reset.
3. Killing the worker after any completed ingest batch resumes from the next durable checkpoint without duplicate or stale vectors.
4. A transient Ollama, Qdrant, object-store, Redis, or PostgreSQL interruption cannot lose an accepted upload or silently mark partial work complete.
5. Two concurrent submissions of the same content create one canonical book/job; two different files with the same filename never overwrite each other.
6. Text search, hybrid fusion, book filtering, image search, book viewing, deletion, retry, and progress reporting have automated regression coverage.
7. Backup and restore are documented and proven by an automated disposable-stack restore drill.
8. Every release has pinned, pullable infrastructure images, a migration path, release notes, and a real rollback boundary.
9. Default network exposure is only the application port; databases, Redis, Qdrant, Ollama, and object storage remain private or loopback-only.
10. No phase advances while its quality gate is red.

---

## Phase 0 — Recover and characterize the known-good baseline

### Task 0.1: Preserve the current WIP without blessing it

**Objective:** Make every current edit recoverable before cleanup.

**Files:** No source edits.

**Steps:**
1. Record `git status`, `git diff`, current HEAD, and Docker image digests.
2. Save the entire dirty tree on a named recovery branch or patch; do not commit it to `main` as a coherent feature.
3. Create a clean stabilization branch from `7073724`.
4. Reapply only independently verified changes later.

**Gate:** Clean worktree at the baseline plus a verified recovery reference containing all current WIP.

### Task 0.2: Establish executable baseline commands

**Objective:** Replace ad-hoc `uv --with ...` commands with canonical project commands.

**Files:**
- Create: `api/pyproject.toml`
- Modify: `api/ruff.toml`, `api/mypy.ini`, `README.md`
- Keep or replace: `api/pytest.ini`

**Steps:**
1. Declare runtime/dev dependency groups and supported Python version.
2. Add canonical commands:
   - `uv sync --frozen --group dev`
   - `uv run pytest -q`
   - `uv run ruff check api tests`
   - `uv run mypy -p api`
3. Ensure tests do not depend on globally installed packages.
4. Keep generated caches ignored.

**Gate:** A clean checkout runs backend checks with the documented commands.

### Task 0.3: Capture behavioral golden tests

**Objective:** Protect the features that made the Server3 deployment useful before refactoring internals.

**Files:**
- Create: `api/tests/fixtures/documents/`
- Create: `api/tests/fixtures/golden_search.json`
- Create: `api/tests/test_ingest_e2e.py`
- Create: `api/tests/test_search_regression.py`
- Create: `api/tests/test_image_pipeline.py`

**Coverage:**
- PDF, EPUB, DOCX, TXT, Markdown, malformed, empty, encrypted, and image-heavy fixtures.
- Deterministic chunk boundaries and normalized text.
- Dense + lexical RRF ranking and book filters.
- Image extraction, deterministic IDs, CLIP indexing behavior, and image delivery.
- Delete and re-ingest behavior.

**Gate:** Golden tests pass against the baseline implementation. Any known baseline defect is recorded as `xfail(strict=True)` with a linked repair task, never hidden.

---

## Phase 1 — Decide storage by contract, not fashion

### Task 1.1: Create a neutral object-storage contract

**Objective:** Remove MinIO/Seaweed naming from application behavior without changing behavior yet.

**Files:**
- Create: `api/api/services/object_store.py`
- Create: `api/tests/test_object_store_contract.py`
- Modify imports in `api/api/main.py`, routers, viewer, image service, and tasks only after contract tests exist.

**Required interface:**
- ensure bucket
- stream upload with declared length
- stream/download to a temporary or durable path
- stat/head
- delete
- existence check
- bounded retry classification
- private API-streamed response support

**Rule:** No browser URL construction and no provider-specific names in models or API responses.

**Gate:** Contract tests run against a fake adapter and the baseline S3 service.

### Task 1.2: Perform an isolated SeaweedFS `weed mini` compatibility spike

**Objective:** Prove or reject SeaweedFS without coupling it to the application refactor.

**Files:**
- Create: `compose.storage-spike.yaml`
- Create: `api/tests/integration/test_seaweedfs_s3.py`
- Create: `docs/adr/0001-object-storage.md`

**Test matrix:** bucket initialization, streamed PUT/GET, HEAD, delete, restart persistence, concurrent upload, large-object multipart behavior, private credentials, corrupt/unavailable volume behavior, and backup/restore.

**Proposed target if green:** one pinned `chrislusf/seaweedfs:4.39` `weed mini` container, one `/data` volume, private network only, pre-created bucket, secret-backed credentials, no public S3 endpoint.

**Fallback:** retain a known-good S3-compatible container behind the same adapter. Do not block ingest stabilization on a storage product swap.

**Gate:** ADR records measured results and a go/no-go decision. No production Compose change before this gate.

### Task 1.3: Make browser transfers API-mediated

**Objective:** Keep only the app public and eliminate public-S3/CORS/SigV4-host complexity.

**Files:**
- Modify: `api/api/routers/upload.py`
- Modify: `api/api/routers/book_viewer.py`
- Modify: `api/api/services/image_svc.py`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/pages/UploadPage.tsx`
- Modify: `web/src/types/api.ts`

**Behavior:**
- multipart upload streams through FastAPI into private object storage;
- immutable key: `books/<book_uuid>/source<extension>`;
- size/type limits enforced before queueing;
- downloads and images stream through authorized API routes;
- no public bucket and no presigned URL host rewriting.

**Gate:** Upload progress works in the UI; a large fixture does not grow API RSS proportional to file size; interrupted uploads leave no queued ingest job and are cleaned safely.

---

## Phase 2 — Rebuild ingestion around a durable ledger

### Task 2.1: Define explicit ingest states and schema

**Objective:** Make PostgreSQL—not Celery—the source of truth.

**Files:**
- Modify: `api/api/models.py`
- Create: `api/migrations/versions/0002_durable_ingestion_jobs.py`
- Create: `api/tests/test_ingestion_models.py`

**Schema:**
- `Book`: immutable source key/hash, processing generation, terminal status, indexed model/version.
- `IngestionJob`: UUID, book FK, stage, state, generation, next chunk index, total chunks, lease owner, lease expiry, attempt count, max attempts, retry timestamp, last error class/message, cancellation flag, created/started/finished timestamps.
- Add uniqueness preventing multiple active jobs for one book/generation.
- Use foreign keys and indexes; replace stringly typed stage transitions with enums.

**Gate:** Upgrade and downgrade migrations pass on empty and populated fixture databases.

### Task 2.2: Introduce a tested state machine

**Objective:** Make invalid transitions impossible and progress monotonic.

**Files:**
- Create: `api/api/services/ingest_state.py`
- Create: `api/tests/test_ingest_state.py`

**Stages:** accepted → source_ready → extracted → chunked → embedding → images → finalizing → indexed, with failed/cancelled/retry-wait states.

**Rules:**
- transitions compare lease token and generation;
- checkpoints commit only after external side effects succeed;
- terminal jobs cannot be silently reopened;
- re-ingest creates a new generation.

**Gate:** Table-driven tests cover every legal/illegal transition, stale leases, cancellation, and concurrent claims.

### Task 2.3: Persist reusable ingest artifacts

**Objective:** Avoid repeating hours of extraction/chunking after a worker restart.

**Files:**
- Create: `api/api/services/ingest_artifacts.py`
- Create: `api/tests/test_ingest_artifacts.py`

**Artifacts:** original source, extracted normalized text/Markdown, deterministic chunk manifest, and image manifest stored under `books/<uuid>/generations/<n>/...` with checksums and schema versions.

**Gate:** Corrupt or version-mismatched artifacts are detected and rebuilt from the nearest valid stage; valid artifacts are reused.

### Task 2.4: Make vector IDs and checkpoints deterministic

**Objective:** Guarantee exactly one current point per canonical chunk/fragment.

**Files:**
- Modify: `api/api/services/qdrant_svc.py`
- Create: `api/tests/test_qdrant_idempotency.py`

**Rules:**
- point identity derives from book UUID + generation + global canonical chunk/fragment index;
- payload records model, dimension, artifact checksum, generation, and canonical index;
- batch upsert uses `wait=True` where required by checkpoint semantics;
- checkpoint `next_chunk_index` advances only after confirmed Qdrant success;
- finalization removes stale prior-generation points only after the new generation is complete.

**Gate:** Crash after batch N, replay with poison fallback, and duplicate delivery produce exactly the expected point set with no stale points.

### Task 2.5: Replace path-based Celery tasks with job-based execution

**Objective:** Make worker loss, broker redelivery, and duplicate dispatch safe.

**Files:**
- Refactor: `api/api/tasks/celery_app.py`
- Create: `api/api/tasks/ingest_tasks.py`
- Create: `api/tests/test_ingest_worker_recovery.py`

**Behavior:**
- task argument is only `job_uuid`;
- atomically claim/renew a DB lease;
- redelivery while another valid lease exists exits safely;
- expired lease resumes from durable stage/checkpoint;
- classify transient vs permanent errors;
- bounded exponential retry with jitter for transient failures;
- malformed files fail once without storms;
- task runtime and Redis visibility settings are documented/configurable and secondary to the DB lease.

**Gate:** Kill a real Celery worker mid-batch, restart it, and verify resume at N+1 with one terminal job.

### Task 2.6: Replace batch fan-out with durable dispatch

**Objective:** Ensure replay cannot enqueue children repeatedly.

**Files:**
- Modify: `api/api/routers/ingest.py`
- Create: `api/api/services/ingest_dispatch.py`
- Create: `api/tests/test_ingest_dispatch.py`

**Behavior:** create/upsert books and jobs transactionally, then enqueue committed job UUIDs. A periodic recovery task dispatches unleased pending/retry jobs so broker loss cannot strand work.

**Gate:** Kill dispatcher after item 2 of 3; recovery produces exactly three jobs and one successful ingest per book.

---

## Phase 3 — Stabilize image extraction and multimodal search

### Task 3.1: Make image extraction observable and idempotent

**Files:**
- Refactor: `api/api/services/image_svc.py`
- Modify: `api/api/tasks/image_tasks.py`
- Create: `api/tests/test_image_recovery.py`

**Changes:**
- remove silent `except: pass` paths;
- deterministic image identity based on source hash/page/xref or manifest index, not local path;
- explicit skipped/failed counts and reasons;
- checkpoint image manifest and indexed count;
- validate dimensions and reject thumbnails/noise by documented thresholds;
- make storage and Qdrant writes idempotent.

**Gate:** Restart mid-image batch resumes without duplicates and reports every skipped/failed image.

### Task 3.2: Version embedding models and collections

**Files:**
- Modify: `api/api/config.py`
- Modify: `api/api/services/qdrant_svc.py`
- Modify: `api/api/services/image_svc.py`
- Create: `api/tests/test_embedding_schema.py`

**Changes:** record model name, revision, dimension, normalization strategy, chunker version, and collection schema. Refuse incompatible writes with a clear reindex command instead of mixing vector spaces.

**Gate:** Model/dimension mismatch fails readiness with remediation; compatible restarts remain green.

### Task 3.3: Add relevance regression evaluation

**Files:**
- Create: `evaluation/queries.json`
- Create: `scripts/evaluate_search.py`
- Create: `docs/relevance.md`

**Metrics:** Recall@K, MRR, nDCG, lexical fallback coverage, image retrieval relevance, latency percentiles. Use redistributable fixtures and allow operators to supply a private evaluation set.

**Gate:** Refactors cannot merge if they exceed agreed relevance or latency regression thresholds.

---

## Phase 4 — Make deployment boring and reproducible

### Task 4.1: Reconcile and simplify Compose

**Files:**
- Rewrite coherently: `compose.yaml`
- Verify overlays: `compose.gpu.yaml`, `compose.images.yaml`
- Modify: `.env.example`, `.gitignore`
- Test: `api/tests/test_deployment_manifest.py`

**Rules:**
- one service name per dependency across Compose, health, doctor, reset, setup, and docs;
- pinned prebuilt infrastructure images; Ollama policy explicit;
- no source-built infrastructure;
- only app port publicly bound by default;
- healthchecks test readiness, not merely open ports;
- named volumes and bounded logs;
- secrets generated with restrictive permissions and never committed;
- CPU baseline and GPU overlay both validate.

**Gate:** base, images, GPU, and combined Compose configs validate from `.env.example`; all referenced image tags/digests pull on amd64.

### Task 4.2: Rebuild setup as an idempotent installer

**Files:**
- Refactor: `setup.sh`
- Create: `scripts/lib/common.sh`
- Create: `tests/shell/test_setup.bats`

**Flow:** supported OS/arch check → Docker/Compose check → GPU/toolkit detection → disk/RAM sizing → secure config generation → Compose validation → pull → migrate → start → model readiness → ingest smoke fixture → final URLs/remediation.

**Rules:** rerun preserves secrets and data; `--dry-run`, `--yes`, `--dev`, CPU/GPU override, noninteractive CI mode; failures print the exact recovery command.

**Gate:** disposable Ubuntu VM tests cover fresh CPU install, fresh NVIDIA install, rerun, interrupted setup, and invalid configuration.

### Task 4.3: Make doctor authoritative

**Files:**
- Refactor: `doctor.sh`
- Add API endpoints: `/api/health/live`, `/api/health/ready`
- Create: `tests/shell/test_doctor.bats`

**Checks:** Compose config, container state, migrations, storage bucket/write probe, DB, Redis, Qdrant collections/schema, Ollama model/dimension, worker heartbeat, stalled/expired jobs, free space, backup freshness, and sample embedding/search. JSON output must be valid JSON.

**Gate:** each intentionally broken dependency yields one precise failing check and remediation.

### Task 4.4: Make reset safe and backup-aware

**Files:**
- Refactor: `reset.sh`
- Create: `backup.sh`, `restore.sh`
- Create: `tests/shell/test_backup_restore.bats`
- Document: `docs/backup-restore.md`

**Backup set:** PostgreSQL dump + Alembic revision, Qdrant snapshots, object-store data/snapshot, app configuration with secrets handled separately, manifest/checksums. Ollama models may be re-pulled.

**Gate:** ingest fixtures, back up, destroy disposable volumes, restore, and verify metadata/object/vector/image/search parity.

### Task 4.5: Make upgrades real and reversible

**Files:**
- Refactor: `upgrade.sh`
- Create: `docs/upgrades.md`
- Test: `tests/shell/test_upgrade.bats`

**Flow:** compatibility preflight → full backup → resolve immutable target version → pull → migration dry-run/check → maintenance boundary → migrate/start → readiness/relevance smoke → record installed version. Rollback may restore old app only when schema-compatible; otherwise restore the backup explicitly.

**Gate:** automated N−1 → N upgrade and forced-failure rollback both restore a working searchable library.

---

## Phase 5 — CI, release engineering, security, and observability

### Task 5.1: Turn CI into a quality gate

**Files:**
- Replace/expand: `.github/workflows/build.yml`
- Create: `.github/workflows/integration.yml`, `.github/dependabot.yml`

**Jobs:** backend pytest/Ruff/mypy/migration check; frontend test/typecheck/build; Compose validation; Docker build; secret scan; dependency/container scan; disposable integration stack; interrupted-ingest recovery; backup/restore drill on scheduled CI. Publish images only after all required jobs pass.

**Gate:** pull requests cannot publish artifacts and cannot merge with a failed required check.

### Task 5.2: Add structured observability

**Files:**
- Create: `api/api/logging.py`, `api/api/metrics.py`
- Modify worker/API startup and ingest services.

**Signals:** request/job/book IDs, stage durations, retries, lease expiry, checkpoint, documents/hour, chunks/second, embedding latency, Qdrant/storage error counts, queue depth, stalled jobs, and disk pressure. No document content or secrets in logs.

**Gate:** a failed fixture ingest can be diagnosed from one job ID without attaching a debugger.

### Task 5.3: Harden public defaults

**Files:**
- Modify configuration/middleware/router limits and deployment docs.
- Create: `docs/security.md`

**Controls:** explicit trusted proxy handling, restrictive CORS, upload size/type validation, rate limiting where exposed, no dependency ports public, secure secret generation, non-root containers, read-only mounts where possible, bounded request/task payloads, security headers, and an explicit authentication/reverse-proxy recommendation. Clarify that publishing the repository and exposing an instance are different threat models.

**Gate:** documented external scan finds only the intended app endpoint; uploads and admin actions cannot be anonymously exposed by accident.

### Task 5.4: Create release and support policy

**Files:**
- Create: `CHANGELOG.md`, `CONTRIBUTING.md`, `SUPPORT.md`, `docs/troubleshooting.md`
- Update: `README.md`

**Contents:** supported OS/architectures, hardware tiers, storage sizing, version policy, upgrade matrix, backup expectations, known limitations, diagnostic bundle command, CPU/GPU behavior, and clear screenshots/first-ingest walkthrough.

**Gate:** a clean user can install from README without undocumented steps; a failure report includes actionable doctor JSON and version information.

---

## Verification pyramid

### Fast checks on every edit

```bash
cd api
uv sync --frozen --group dev
uv run pytest -q
uv run ruff check api tests
uv run mypy -p api

cd ../web
npm ci
npm test -- --run
npm run typecheck
npm run build

cd ..
docker compose --env-file .env.example -f compose.yaml config --quiet
docker compose --env-file .env.example -f compose.yaml -f compose.gpu.yaml config --quiet
git diff --check
```

### Integration checks before each phase closes

- fresh migrations on PostgreSQL;
- real object-store contract;
- real Qdrant upsert/search/delete;
- real Redis/Celery worker;
- Ollama fixture embedding or a deterministic test double for ordinary CI;
- API/UI upload → ingest → search → image → delete;
- worker kill/restart after each checkpoint;
- duplicate dispatcher/delivery tests;
- backup/destroy/restore drill.

### Release checks

- clean amd64 Ubuntu VM install;
- supported NVIDIA host install;
- CPU-only install;
- installer rerun;
- N−1 upgrade and rollback;
- 8+ hour soak ingest using redistributable/generated corpus;
- disk-full, service-restart, network-timeout, and host-reboot fault injection;
- relevance and latency baseline comparison.

---

## Proposed implementation order

1. Preserve WIP and restore a clean baseline.
2. Establish canonical tooling and golden behavioral tests.
3. Fix the broken upload → job boundary without changing storage product.
4. Implement durable jobs, leases, artifacts, and deterministic vector IDs.
5. Prove worker crash/recovery and duplicate-delivery safety.
6. Stabilize image extraction/indexing and relevance evaluation.
7. Run the object-storage ADR spike; adopt SeaweedFS `mini` only if it clears the contract and recovery gates.
8. Reconcile Compose, setup, doctor, reset, backup/restore, and upgrades.
9. Add CI, observability, security defaults, and release documentation.
10. Execute fresh-install, upgrade, restore, and long-ingest soak gates before declaring 1.0 stable.

## Review decisions requested

1. **Baseline policy:** recommended—quarantine all current uncommitted WIP, start from `7073724`, and selectively reapply only proven pieces.
2. **Storage policy:** recommended—neutral S3 adapter first; SeaweedFS `weed mini` is a candidate, not an assumption, until its isolated contract/recovery gate passes.
3. **Upload policy:** recommended—API-mediated streaming with only the app publicly exposed; no public S3 endpoint.
4. **Compatibility policy:** confirm whether a future release must import the existing Server3 PostgreSQL/MinIO/Qdrant corpus or only support fresh installations. The architecture supports either, but migration tooling changes scope.
5. **Release target:** recommended—declare the first release that passes all acceptance gates as `1.0.0`; do not market the intermediate stabilization branch as production-ready.
