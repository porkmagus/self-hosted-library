# Security model

Self-Hosted Library is designed as a private single-machine service, not a turnkey public multi-user SaaS. By default only the application port is published and it binds to `127.0.0.1`; PostgreSQL, Redis, Qdrant, SeaweedFS, and Ollama stay on the private Compose network.

## Safe exposure

For LAN or Internet access, put an authenticated reverse proxy or identity-aware gateway in front of the application. Terminate TLS there, restrict upload/admin routes, set an explicit `CORS_ORIGINS`, and configure the proxy to replace rather than append forwarding headers. The application itself does not provide user accounts or authorization boundaries.

Containers use an unprivileged application user, drop Linux capabilities, enable no-new-privileges, and bound Docker logs. Responses include CSP, frame, MIME-sniffing, referrer, and permissions-policy headers. Upload extensions and filenames are validated before durable acceptance; object storage remains private and transfers pass through the API.

## Secrets and backups

`setup.sh` generates deployment credentials and local identity files with mode `0600`. Never commit local configuration or `.state`. Backup archives contain deployment configuration and must be encrypted or stored on a trusted offline destination.

Keep Docker, the host OS, infrastructure images, and application releases patched. Run dependency audits in CI, review Dependabot updates, and take a verified backup before upgrading.
