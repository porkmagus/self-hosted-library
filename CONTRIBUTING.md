# Contributing

Contributions are welcome through focused pull requests. Open an issue before changing storage, queueing, schema, or deployment architecture so compatibility and recovery boundaries can be reviewed first.

## Development checks

Backend:

```bash
cd api
uv run --python 3.13 --with-requirements requirements-dev.txt pytest -q
uv run --python 3.13 --with-requirements requirements-dev.txt ruff check api tests migrations
uv run --python 3.13 --with-requirements requirements-dev.txt ruff format --check api tests migrations
uv run --python 3.13 --with-requirements requirements-dev.txt mypy api
```

Frontend:

```bash
cd web
npm ci
npm test
npm run typecheck
npm run build
```

Use tests first for behavior changes. Never weaken a durable-ingestion fencing assertion merely to make a refactor pass. Do not include books, model weights, credentials, generated backups, or private evaluation data. Keep commits conventional and explain migration, rollback, and operator impact in the pull request.
