#!/usr/bin/env python3
"""Safely dry-run or selectively re-embed normalized text for one book."""
from __future__ import annotations

import argparse

from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from api.config import settings
from api.services.ollama_svc import get_embedding_batch
from api.services.qdrant_svc import get_qdrant_client
from api.services.search_cache import bump_index_generation
from api.services.search_utils import normalize_display_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-id", required=True, help="Exact indexed book UUID")
    parser.add_argument("--apply", action="store_true", help="Write normalized payloads and replacement vectors")
    args = parser.parse_args()
    client = get_qdrant_client()
    query_filter = Filter(must=[FieldCondition(key="book_id", match=MatchValue(value=args.book_id))])
    offset = None
    scanned = changed = updated = 0
    while True:
        records, offset = client.scroll(
            collection_name=settings.QDRANT_COLLECTION,
            scroll_filter=query_filter,
            limit=128,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        pending: list[tuple[object, dict[str, object], str]] = []
        for record in records:
            scanned += 1
            payload = dict(record.payload or {})
            original = str(payload.get("content", ""))
            normalized = normalize_display_text(original)
            if normalized and normalized != original:
                changed += 1
                payload["content"] = normalized
                pending.append((record.id, payload, normalized))
        if args.apply and pending:
            embeddings = get_embedding_batch([item[2] for item in pending])
            points = [
                PointStruct(id=item[0], vector=embedding, payload=item[1])
                for item, embedding in zip(pending, embeddings, strict=True)
                if embedding is not None
            ]
            if points:
                client.upsert(collection_name=settings.QDRANT_COLLECTION, points=points, wait=True)
                updated += len(points)
        if offset is None:
            break
    if args.apply and updated:
        bump_index_generation()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{mode}: book={args.book_id} scanned={scanned} changed={changed} updated={updated}")


if __name__ == "__main__":
    main()
