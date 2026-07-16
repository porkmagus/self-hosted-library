import { useCallback, useState } from "react"
import { C } from "../types"
import { api } from "../lib/api"
import { formatSize } from "../lib/format"
import { ErrorBanner } from "../components"

const ACCEPT = ".pdf,.epub,.doc,.docx,.txt,.md,.htm,.html"

export function UploadPage() {
  const [status, setStatus] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [pct, setPct] = useState(0)
  const [dragOver, setDragOver] = useState(false)

  const pick = useCallback((f: File | null) => {
    setFile(f)
    setError(null)
    setStatus("")
    setPct(0)
  }, [])

  const handleUpload = async () => {
    if (!file || busy) return
    setBusy(true)
    setError(null)
    setStatus("Uploading securely through the API…")
    setPct(0)
    try {
      await api.uploadFile(file, setPct)
      setStatus(`Queued "${file.name}" for ingestion`)
      setPct(100)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setStatus("")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h2 className="page-title">UPLOAD</h2>
      {error && <ErrorBanner message={error} onRetry={() => void handleUpload()} />}

      <div
        className={`dropzone ${dragOver ? "active" : ""}`}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => {
          e.preventDefault()
          setDragOver(false)
          const f = e.dataTransfer.files?.[0] || null
          if (f) pick(f)
        }}
      >
        <div style={{
          width: 52, height: 52, margin: "0 auto 14px", borderRadius: "50%",
          border: `1px solid ${C.gold}55`, display: "flex", alignItems: "center", justifyContent: "center",
          color: C.gold, fontSize: 22, boxShadow: `0 0 24px ${C.goldGlow}`,
        }}>↑</div>
        <div style={{ color: C.gold, marginBottom: 8, letterSpacing: 0.14, fontFamily: "var(--font-display)" }}>
          DROP PDF / EPUB / TXT / MD
        </div>
        <div className="muted" style={{ marginBottom: 16 }}>or choose a file from disk</div>
        <input
          type="file"
          accept={ACCEPT}
          disabled={busy}
          onChange={e => pick(e.target.files?.[0] || null)}
          style={{ color: C.text, fontSize: 14 }}
        />
      </div>

      {file && (
        <div className="card" style={{ marginTop: 18 }}>
          <div style={{ color: C.gold, fontSize: 16, marginBottom: 6, fontFamily: "var(--font-display)" }}>{file.name}</div>
          <div className="muted">{formatSize(file.size)}</div>
          {(busy || pct > 0) && (
            <div style={{ marginTop: 14 }}>
              <div className="progress"><span style={{ width: `${pct}%` }} /></div>
              <div className="muted" style={{ marginTop: 8 }}>{pct}%</div>
            </div>
          )}
        </div>
      )}

      <div style={{ marginTop: 18 }}>
        <button type="button" className="btn" onClick={() => void handleUpload()} disabled={!file || busy}>
          {busy ? "WORKING…" : "UPLOAD & INGEST"}
        </button>
      </div>

      {status && (
        <div className="card" style={{ marginTop: 16 }}>
          <div style={{ color: status.startsWith("Queued") ? C.success : C.textDim, fontSize: 14 }}>{status}</div>
        </div>
      )}
    </div>
  )
}
