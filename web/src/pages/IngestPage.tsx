import { useCallback, useEffect, useRef, useState } from "react"
import type { IngestProgress } from "../types"
import { C } from "../types"
import { api } from "../lib/api"
import { ingestProgressLabel } from "../lib/ingestPresentation"
import { ErrorBanner, Skeleton } from "../components"

export function IngestPage() {
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [status, setStatus] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [scanInbox, setScanInbox] = useState(true)
  const [busy, setBusy] = useState(false)
  const [loadingStatus, setLoadingStatus] = useState(true)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadAggregate = useCallback(async () => {
    setLoadingStatus(true)
    try {
      const data = await api.ingestStatus()
      setProgress(data)
    } catch (e) {
      console.error(e)
    } finally {
      setLoadingStatus(false)
    }
  }, [])

  useEffect(() => {
    void loadAggregate()
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [loadAggregate])

  const poll = async (tid: string) => {
    try {
      const data = await api.ingestProgress(tid)
      setProgress(data)
      if (data.status === "completed" || data.status === "completed_with_errors") {
        if (pollingRef.current) clearInterval(pollingRef.current)
        setBusy(false)
        setStatus(data.status === "completed" ? "Done!" : `Completed with ${data.failed} failed`)
      }
    } catch (e) {
      console.error(e)
    }
  }

  const startIngest = async () => {
    setBusy(true)
    setError(null)
    setStatus("Scanning…")
    try {
      const data = await api.ingestLocal(scanInbox)
      const count = (data as { book_count?: number; count?: number }).book_count
        ?? (data as { count?: number }).count
        ?? 0
      const taskId = (data as { task_id?: string }).task_id || ""
      setStatus(count ? `Queued ${count} books` : "No books queued")
      if (taskId) {
        if (pollingRef.current) clearInterval(pollingRef.current)
        pollingRef.current = setInterval(() => void poll(taskId), 2000)
      } else {
        setBusy(false)
        await loadAggregate()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setStatus("")
      setBusy(false)
    }
  }

  const pct = typeof progress?.progress_pct === "number" ? progress.progress_pct : null

  return (
    <div>
      <h2 className="page-title">INGEST</h2>
      {error && <ErrorBanner message={error} onRetry={() => void startIngest()} />}

      <label style={{
        display: "flex", alignItems: "center", gap: 10, color: C.textDim,
        marginBottom: 20, fontSize: 14, cursor: "pointer",
      }}>
        <input
          type="checkbox"
          checked={scanInbox}
          onChange={e => setScanInbox(e.target.checked)}
          style={{ accentColor: C.gold, width: 16, height: 16 }}
          disabled={busy}
        />
        Scan inbox directory for new books
      </label>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
        <button type="button" className="btn" onClick={() => void startIngest()} disabled={busy}>
          {busy ? "RUNNING…" : "START INGESTION"}
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => void loadAggregate()}>
          REFRESH STATUS
        </button>
      </div>

      {loadingStatus && !progress && <Skeleton height={110} count={1} />}

      {progress && (
        <div className="card" style={{ marginTop: 20 }}>
          <div style={{ color: C.gold, fontSize: 15, marginBottom: 12, fontFamily: "var(--font-display)", letterSpacing: 0.08 }}>
            Progress{" "}
            <span style={{
              display: "inline-block", padding: "3px 10px", fontSize: 10, letterSpacing: 0.1,
              background: `${C.gold}18`, color: C.gold, borderRadius: 999,
              border: `1px solid ${C.gold}40`, marginLeft: 8, verticalAlign: "middle",
            }}>
              {(progress.status || "unknown").toUpperCase()}
            </span>
          </div>
          {pct !== null && (
            <div>
              <div className="progress"><span style={{ width: `${pct}%` }} /></div>
              <div className="muted" style={{ marginTop: 10 }}>
                {ingestProgressLabel(progress)}
              </div>
            </div>
          )}
        </div>
      )}

      {status && (
        <div className="card" style={{ marginTop: 12 }}>
          <div style={{ color: C.textDim, fontSize: 14 }}>{status}</div>
        </div>
      )}
    </div>
  )
}
