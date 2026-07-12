# Self-Hosted Library

A GPU-first, self-hosted document ingestion, semantic retrieval, and research system.

This repository is an extracted development baseline from a working private deployment. It contains application source only: no books, images, model binaries, indexes, runtime volumes, private domains, or deployment credentials.

## Current stack

- React/Vite frontend served by nginx
- FastAPI API
- Celery worker and Redis
- PostgreSQL metadata
- Qdrant vector search
- MinIO object storage
- Ollama text embeddings
- Optional CLIP image search and cross-encoder reranking

## Repository status

The source has been transferred into an independent Mac workspace so it can be reorganized for a public-quality GitHub deployment. The current Compose file is a functional production-derived baseline, not yet the final random-user installer.

Planned deployment contract:

- Linux x86-64 and NVIDIA RTX as the primary supported target
- Prebuilt, versioned container images
- Host requirements limited to Docker, the NVIDIA driver, and NVIDIA Container Toolkit
- GPU and driver detection during setup
- No host Python, Node, CUDA toolkit, or user-selected PyTorch wheels
- Persistent user library and application state kept outside Git
- Versioned updates with backup, migration, health checks, and rollback

## Development baseline

Copy the example environment before validating Compose:

```bash
cp .env.example .env
# Replace every CHANGE_ME value before starting services.
docker compose config --quiet
```

Do not commit `.env`.

## Data safety

The following are deliberately excluded from version control:

- `.env`
- `data/` and future `library/` source content
- `models/`
- Qdrant/PostgreSQL/Redis/MinIO/Ollama state
- generated caches, build output, and reports

Reset and installer tooling will be added before this is presented as a deployable release.
