# Self-Hosted Library

A GPU-first, self-hosted document ingestion, semantic retrieval, and research system. One image, one command to start.

## Quick start

```bash
git clone https://github.com/porkmagus/self-hosted-library.git
cd self-hosted-library
./setup.sh
```

That's it. The script detects your hardware, generates credentials, pulls the embedding model, and starts all services. Open http://localhost:8000 when it finishes.

Drop PDFs (or EPUBs, DOCXs, text files) into `data/inbox/` and click "Start Ingestion" in the web UI.

## Requirements

- **Docker** and **Docker Compose** (plugin or standalone)
- **Linux x86-64** (primary target; macOS with Docker Desktop works for dev)
- **NVIDIA GPU** optional — CPU-only mode works, embeddings are just slower
- **~20 GB** free disk space (models + indexes + your documents)

## Scripts

| Script | Purpose |
|--------|---------|
| `./setup.sh` | One-command bootstrap: detect hardware, generate `.env`, pull model, start services |
| `./doctor.sh` | Health check: Docker, GPU, disk, ports, containers, API, embedding |
| `./reset.sh` | Stop services. `--volumes` wipes data. `--hard` also removes `.env` |
| `./upgrade.sh` | Safe update: backup, pull, migrate, health check, rollback on failure |

## Architecture

```
┌──────────────────────────────────────┐
│  FastAPI + React (single container)  │
│  :8000 — / (UI) + /api/* (REST)      │
└──────────────┬───────────────────────┘
               │
    ┌──────────┼──────────────┬──────────┐
    │          │              │          │
┌───▼──┐ ┌────▼──┐ ┌──────┐ ┌▼─────┐ ┌──▼───┐
│Qdrant│ │Postgre│ │Redis │ │MinIO │ │Ollama│
│:6333 │ │:5432 │ │:6379 │ │:9000 │ │:11434│
└──────┘ └──────┘ └──────┘ └──────┘ └──────┘
```

- **Qdrant** — vector search (dense + keyword hybrid with RRF fusion)
- **PostgreSQL** — book metadata, ingestion state, app settings
- **Redis** — Celery broker, search cache, rate limiting
- **MinIO** — S3-compatible object storage for uploaded files
- **Ollama** — local embedding model (bge-large, 1024-dim)
- **Optional** — CLIP image search, cross-encoder reranking

## GPU acceleration

`compose.yaml` is CPU-safe by default. If `setup.sh` detects an NVIDIA GPU with Container Toolkit, it merges `compose.gpu.yaml` automatically. To enable manually:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml up -d
```

## Deployment

Pre-built images are published to `ghcr.io/porkmagus/self-hosted-library` on every tag. `setup.sh` uses them by default. To build locally instead:

```bash
./setup.sh --dev
```

Or use the compose files directly:

```bash
# Pre-built images (default)
docker compose -f compose.yaml -f compose.images.yaml up -d

# Local build
docker compose up -d

# With GPU
docker compose -f compose.yaml -f compose.images.yaml -f compose.gpu.yaml up -d
```

## Development

```bash
# Backend
cd api
uv run --python 3.11 --with pytest --with redis --with pydantic-settings \
  --with fastapi --with sqlalchemy --with celery --with minio \
  --with psycopg2-binary --with qdrant-client pytest
uvx ruff check api tests
uv run --python 3.13 --with mypy mypy -p api --config-file mypy.ini

# Frontend
cd web
npm test && npm run typecheck && npm run build
```

## License

Source code only. No books, images, models, or private data are included in this repository.
