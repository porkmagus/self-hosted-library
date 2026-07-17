# Self-Hosted Library

A private, self-hosted document ingestion and multimodal search system. PostgreSQL owns workflow and visibility, workers resume durable ingestion jobs, SeaweedFS stores private source/image objects, and Qdrant serves text and image retrieval.

## Quick start

Requirements:

- Docker Engine or Docker Desktop with Compose v2
- OpenSSL and Python 3 on the host
- Approximately 20 GiB of free disk space before adding your library
- An NVIDIA GPU is optional; CPU-only Ollama works more slowly

```bash
git clone https://github.com/porkmagus/self-hosted-library.git
cd self-hosted-library
./setup.sh
```

Setup preserves an existing `.env`, generates credentials and the private SeaweedFS identity with mode `0600`, starts dependencies, pulls the configured Ollama model, provisions the private bucket, applies Alembic migrations, and verifies the API. Open <http://localhost:8000> unless `APP_PORT` was changed.

Production uses pinned prebuilt images from `compose.yaml`. Local source builds use:

```bash
./setup.sh --dev
# equivalent overlay: docker compose -f compose.yaml -f compose.dev.yaml up -d
```

For an NVIDIA host with Container Toolkit, include the explicit GPU overlay:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml up -d
```

Only the application port is published by default. PostgreSQL, Redis, Qdrant, SeaweedFS, and Ollama remain on the private Compose network.

## Operator lifecycle

| Command | Purpose |
|---|---|
| `./setup.sh` | Idempotent bootstrap; `--regenerate` explicitly replaces configuration |
| `./doctor.sh` | Container, API dependency, model, and private object-store checks |
| `./doctor.sh --json` | Machine-readable health report generated as valid JSON |
| `./reset.sh` | Stop services while preserving managed data |
| `./reset.sh --volumes` | Destructively remove all managed data after typed confirmation |
| `./backup.sh` | Quiesced PostgreSQL/Qdrant/SeaweedFS backup with manifest and checksums |
| `./restore.sh BACKUP_DIR` | Checksum-verified destructive restore followed by migration and health checks |
| `./upgrade.sh VERSION` | Verified backup, immutable image selection, migration, restart, and health gate |

### Backup and restore

PostgreSQL, Qdrant, and SeaweedFS are authoritative. Redis is queue/cache state; Ollama and model caches are downloadable and excluded from backups.

```bash
./backup.sh
./backup.sh --output=/secure/off-host/library-backup
./restore.sh /secure/off-host/library-backup
```

A backup contains deployment credentials in `config.env`; store it as sensitive data and copy it off-host. Restore replaces this Compose project’s PostgreSQL, Qdrant, and SeaweedFS contents and therefore requires typed confirmation (`--yes` is the explicit automation override). By default it restores the matching configuration; `--keep-config` is only safe when database and S3 credentials are compatible.

### Upgrade and recovery

Mutable application tags are rejected. Select a released tag or immutable digest through the version argument:

```bash
./upgrade.sh v1.1.0
```

Upgrade first creates and verifies a full backup. Database migrations are not claimed to be container-only rollback safe. If a migration or health gate fails, the script prints the exact `restore.sh` command for the pre-upgrade artifact.

### Dry runs

```bash
./setup.sh --dry-run
./reset.sh --volumes --dry-run
./backup.sh --dry-run
./restore.sh --dry-run /path/to/backup
./upgrade.sh --dry-run v1.1.0
```

Dry runs do not generate credentials, modify configuration, start containers, or delete data.

## Architecture

```text
Browser ──> FastAPI + React :8000
                 │
        ┌────────┼───────────┬───────────┬──────────┐
        │        │           │           │          │
   PostgreSQL  Redis      Qdrant     SeaweedFS    Ollama
   workflow   Celery/     text+image  private S3   embeddings
   authority  cache       vectors     objects
                 │
          Celery worker + separate Beat scheduler
```

- **PostgreSQL** is authoritative for job leases, checkpoints, deletion, generation, and activation identity.
- **Qdrant** receives hidden claim-scoped points; search is prefiltered and reauthorized against PostgreSQL.
- **SeaweedFS `weed mini`** provides one private S3-compatible service backed by `seaweedfs_data`.
- **Redis** carries Celery delivery, rate limits, and reconstructible search cache state.
- **Ollama** serves the configured embedding model.
- **App, worker, and Beat** are separate services so scheduler uniqueness is not tied to worker replicas.

Named volumes:

- `postgres_data`, `qdrant_data`, and `seaweedfs_data`: authoritative
- `redis_data`: reconstructible queue/cache state
- `ollama_data`: downloadable Ollama models; application model caches are reconstructible container state

## Development verification

```bash
cd api
uv run --python 3.13 --with-requirements requirements-dev.txt pytest -q
uv run --python 3.13 --with-requirements requirements-dev.txt ruff check api tests migrations
uv run --python 3.13 --with-requirements requirements-dev.txt ruff format --check api tests migrations
uv run --python 3.13 --with-requirements requirements-dev.txt mypy api

cd ../web
npm test
npm run typecheck
npm run build
```

Durable-ingestion invariants are documented in [`docs/adr/0002-durable-ingestion.md`](docs/adr/0002-durable-ingestion.md).

Before exposing an instance beyond loopback, read [`docs/security.md`](docs/security.md). For diagnostics and recovery guidance, see [`docs/troubleshooting.md`](docs/troubleshooting.md) and [`SUPPORT.md`](SUPPORT.md).

## License

Source code only. No books, images, models, credentials, or private data are included in this repository.
