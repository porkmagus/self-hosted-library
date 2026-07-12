import { expect, it } from "vitest"
import { ingestProgressLabel } from "./ingestPresentation"
import type { IngestProgress } from "../types"

it("formats durable aggregate ingestion progress", () => {
  const progress: IngestProgress = {
    status: "processing",
    progress: 60,
    progress_pct: 60,
    total_books: 5,
    indexed: 3,
    in_progress: 1,
    failed: 1,
    qdrant_chunks: 42,
  }
  expect(ingestProgressLabel(progress)).toBe("60% — 3/5 indexed · 1 failed")
})
