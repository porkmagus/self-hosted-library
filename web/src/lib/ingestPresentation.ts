import type { IngestProgress } from "../types"

export function ingestProgressLabel(progress: IngestProgress): string {
  const failed = progress.failed ? ` · ${progress.failed} failed` : ""
  return `${progress.progress_pct}% — ${progress.indexed}/${progress.total_books} indexed${failed}`
}