import type { IngestProgress } from "../types"

export function ingestStatusLabel(status: string | undefined, failed: number | undefined): string {
  if (!status) return "UNKNOWN"
  switch (status) {
    case "completed":
      return "COMPLETED"
    case "completed_with_errors":
      return failed ? `COMPLETED (${failed} ERRORS)` : "COMPLETED"
    case "in_progress":
      return "IN PROGRESS"
    case "idle":
      return "IDLE"
    default:
      return status.toUpperCase()
  }
}

export function ingestProgressLabel(progress: IngestProgress): string {
  const failed = progress.failed ? ` · ${progress.failed} failed` : ""
  return `${progress.progress_pct}% — ${progress.indexed}/${progress.total_books} indexed${failed}`
}