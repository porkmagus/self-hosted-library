# Changelog

All notable changes are documented here. This project follows Semantic Versioning once the first stable tag is published.

## Unreleased

### Added

- Durable PostgreSQL ingestion ledger with fenced leases, resumable checkpoints, transactional dispatch, and activation reconciliation.
- Private API-mediated source and image delivery through a neutral S3-compatible object-store adapter.
- Single-node SeaweedFS storage, separate Celery Beat service, backup/restore tooling, and authoritative diagnostics.
- CI quality gates for backend, frontend, lifecycle manifests, dependency audits, and release-image builds.

### Changed

- Infrastructure is pinned to PostgreSQL 18.4, Redis 8.8, Qdrant 1.18.2, SeaweedFS 4.39, and Ollama 0.12.10.
- Only the application port is published, and it binds to loopback by default.

### Security

- Application containers run as an unprivileged user with all Linux capabilities dropped and no-new-privileges enabled.
- Browser security headers and validated request identifiers are applied to every response.

No stable release has been declared yet. Operators should back up authoritative data before testing unreleased builds.
