# Troubleshooting

Start with:

```bash
./doctor.sh
./doctor.sh --json > doctor.json
```

## Application does not start

Run `docker compose config --quiet`, then inspect `docker compose ps` and `docker compose logs app`. A missing local configuration means `./setup.sh` has not completed. Dependency failures are reported separately by the health endpoint and doctor.

## Upload is accepted but does not progress

Inspect `docker compose logs worker beat`, confirm Redis and PostgreSQL pass doctor, and check free disk space. The durable ledger retains accepted jobs; restarting a worker should resume from the last committed checkpoint rather than starting over. Do not manually edit job rows or Qdrant payloads.

## Search is empty or stale

Confirm Qdrant health, embedding model name/dimension, and collection settings. A model or schema mismatch requires a deliberate re-index; do not mix incompatible vectors in the same collection.

## Recovery

Create backups with `./backup.sh`. A restore is destructive and verifies checksums before replacing PostgreSQL, Qdrant, and SeaweedFS volumes. Use `./restore.sh BACKUP_DIR`, then rerun doctor. Upgrade failures print the exact restore command for the pre-upgrade backup.

When requesting support, attach sanitized doctor JSON and the narrow relevant logs, never local configuration, credentials, backups, or private documents.
