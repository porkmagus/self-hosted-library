# ADR 0001: Private S3-compatible object storage

- **Status:** Accepted for implementation; release gate remains open
- **Date:** 2026-07-16

## Context

The application needs a small S3 surface: create/check a bucket, stream PUT/GET, HEAD, DELETE, and durable persistence across container replacement. The deployment target is a single Ubuntu server. The object store is an internal dependency, not a public browser endpoint.

MinIO Community Edition previously worked well, but its current distribution and support direction no longer provide a clean, actively maintained default for this public project. AIStor is not acceptable as the default because it introduces commercial licensing/support friction. The application must not depend on provider-specific administration or public URL behavior.

## Decision

1. Put object operations behind `api/api/services/object_store.py`.
2. Keep only the application port public. Uploads and downloads stream through the API over the private Docker network.
3. Use one pinned SeaweedFS container running `weed mini` for the single-node deployment if the complete integration and recovery gates pass.
4. Persist `/data` in one named volume and make the application perform idempotent bucket creation.
5. Do not deploy separate master, volume, filer, and S3 containers on one host. That topology is reserved for multi-host scale/HA requirements.
6. Keep an implementation-independent contract so another maintained S3 backend can replace SeaweedFS without changing routers, workers, or models.

## Evidence

The official SeaweedFS documentation describes `weed mini` as an all-in-one master, volume, filer, S3 gateway, maintenance worker, and administration process suitable for many single-node production deployments. SeaweedFS is independently developed under Apache-2.0.

An isolated `chrislusf/seaweedfs:4.39` test on 2026-07-16 verified:

- authenticated S3 access;
- explicit bucket creation;
- object PUT and byte-verified GET;
- complete container removal and replacement using the same `/data` volume;
- object persistence after replacement;
- object DELETE;
- cleanup of the disposable container and volume.

The documented `S3_BUCKET`/`-bucket` startup convenience did not pre-create a bucket in the tested 4.39 image despite the effective environment and arguments being present. Therefore startup correctness will not rely on that convenience; application initialization must create/check the bucket and fail readiness if it cannot.

## Alternatives considered

### AIStor

Rejected as the project default. It creates commercial licensing/support friction and does not match the project's low-friction open-source deployment goal.

### Legacy MinIO Community Edition

Technically proven by the original deployment, but not selected as the forward default because the actively maintained distribution path is unclear. The S3 contract preserves the ability to use a compatible existing MinIO deployment.

### Garage

Mature and operationally thoughtful, but AGPL-3.0, optimized primarily for multi-node/geographically distributed replication, and missing parts of the wider S3 API. Its implemented core operations are sufficient for this app, so it remains a fallback candidate.

### RustFS

Apache-2.0 and MinIO-like, but newer; its own feature table still marks distributed mode and lifecycle behavior as under testing. Revisit after more production maturity.

### Ceph RGW

Rejected for the single-machine default because its operational footprint is disproportionate to this application's storage contract.

## Release gate

SeaweedFS becomes the shipped default only after automated tests prove streamed upload/download, large-object behavior, private authentication, restart persistence, concurrent object names, application readiness, backup/destroy/restore, and ingest recovery using the real Compose stack.
