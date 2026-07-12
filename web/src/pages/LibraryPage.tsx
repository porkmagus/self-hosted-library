import { useState, useEffect, useCallback } from "react"
import type { BookFull } from "../types"
import { C } from "../types"
import { Badge, EmptyState, ErrorBanner, Skeleton } from "../components"
import { api } from "../lib/api"
import { formatSize } from "../lib/format"
import { useDebouncedValue } from "../hooks"

export function LibraryPage() {
  const [books, setBooks] = useState<BookFull[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQ, setSearchQ] = useState("")
  const debouncedSearch = useDebouncedValue(searchQ, 300)
  const [offset, setOffset] = useState(0)
  const [sortBy, setSortBy] = useState<"title" | "date" | "size">("date")
  const [groupBy, setGroupBy] = useState<"none" | "author" | "status">("none")
  const [selectedBook, setSelectedBook] = useState<BookFull | null>(null)
  const LIMIT = 100

  const loadBooks = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) })
      if (debouncedSearch) params.set("search", debouncedSearch)
      const data = await api.books(params)
      let sorted = data.books || []
      if (sortBy === "title") sorted = [...sorted].sort((a, b) => a.title.localeCompare(b.title))
      else if (sortBy === "size") sorted = [...sorted].sort((a, b) => (b.file_size_bytes || 0) - (a.file_size_bytes || 0))
      else sorted = [...sorted].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))
      setBooks(sorted)
      setTotal(data.total || 0)
    } catch (e) {
      console.error(e)
      setError(e instanceof Error ? e.message : String(e))
    }
    setLoading(false)
  }, [debouncedSearch, offset, sortBy])

  useEffect(() => { setOffset(0) }, [debouncedSearch, sortBy, groupBy])
  useEffect(() => { void loadBooks() }, [loadBooks])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && selectedBook) setSelectedBook(null)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [selectedBook])

  const statusColor = (s: string) =>
    s === "indexed" ? C.success : s === "embedding" ? C.gold : s === "failed" ? C.danger : C.textMuted

  const grouped: Record<string, BookFull[]> = {}
  if (groupBy === "author") {
    books.forEach(b => {
      const key = b.author || "(No Author)"
      if (!grouped[key]) grouped[key] = []
      grouped[key].push(b)
    })
  }
  const groupedByStatus: Record<string, BookFull[]> = {}
  if (groupBy === "status") {
    books.forEach(b => {
      const key = b.status || "unknown"
      if (!groupedByStatus[key]) groupedByStatus[key] = []
      groupedByStatus[key].push(b)
    })
  }

  const stats = books.reduce((acc, b) => {
    acc.total += 1
    acc.chunks += b.indexed_chunks || 0
    acc.size += b.file_size_bytes || 0
    if (b.status === "indexed") acc.indexed += 1
    if (b.status === "failed") acc.failed += 1
    return acc
  }, { total: 0, indexed: 0, failed: 0, chunks: 0, size: 0 })

  if (selectedBook) {
    return (
      <div className="overlay" style={{ display: "flex", alignItems: "center", justifyContent: "center" }} onClick={() => setSelectedBook(null)}>
        <div className="modal-panel" onClick={e => e.stopPropagation()}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 16 }}>
            <div style={{ color: C.gold, fontSize: 18, fontFamily: "var(--font-display)", lineHeight: 1.35 }}>{selectedBook.title}</div>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSelectedBook(null)}>✕</button>
          </div>
          {selectedBook.author && <div style={{ color: C.textDim, marginBottom: 8 }}>Author: {selectedBook.author}</div>}
          <div style={{ color: C.textDim, marginBottom: 8 }}>File: {selectedBook.original_filename}</div>
          <div style={{ color: C.textDim, marginBottom: 8 }}>Size: {formatSize(selectedBook.file_size_bytes || 0)}</div>
          <div style={{ color: C.textDim, marginBottom: 8 }}>
            Chunks: {selectedBook.indexed_chunks || 0}/{selectedBook.total_chunks || 0}
          </div>
          <div style={{ marginBottom: 18 }}>
            <Badge color={statusColor(selectedBook.status)}>{(selectedBook.status || "unknown").toUpperCase()}</Badge>
          </div>
          <button type="button" className="btn" onClick={() => window.open(api.bookPdfUrl(selectedBook.uuid), "_blank")}>
            OPEN PDF
          </button>
        </div>
      </div>
    )
  }

  const renderList = (list: BookFull[]) => list.map(b => (
    <div key={b.uuid} className="card card-interactive" style={{ marginBottom: 10 }} onClick={() => setSelectedBook(b)}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ color: C.gold, fontSize: 15, marginBottom: 4, fontFamily: "var(--font-display)" }}>{b.title}</div>
          <div className="muted text-wrap">
            {b.author || "Unknown author"}
            {b.file_size_bytes ? ` · ${formatSize(b.file_size_bytes)}` : ""}
            {` · ${b.indexed_chunks || 0} chunks`}
          </div>
        </div>
        <Badge color={statusColor(b.status)}>{(b.status || "?").toUpperCase()}</Badge>
      </div>
    </div>
  ))

  return (
    <div>
      <h2 className="page-title">LIBRARY</h2>

      <div className="stat-grid">
        {[
          { label: "PAGE TOTAL", value: String(stats.total), color: C.gold },
          { label: "INDEXED", value: String(stats.indexed), color: C.success },
          { label: "CHUNKS", value: stats.chunks.toLocaleString(), color: C.info },
          { label: "STORAGE", value: formatSize(stats.size), color: C.textDim },
          { label: "FAILED", value: String(stats.failed), color: stats.failed ? C.danger : C.textMuted },
        ].map(s => (
          <div key={s.label} className="stat-card">
            <div className="label">{s.label}</div>
            <div className="value" style={{ color: s.color }}>{s.value}</div>
          </div>
        ))}
      </div>

      <div className="toolbar">
        <input
          className="input"
          style={{ maxWidth: 360 }}
          placeholder="Filter titles…"
          value={searchQ}
          onChange={e => setSearchQ(e.target.value)}
        />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <select className="select" style={{ width: "auto" }} value={sortBy} onChange={e => setSortBy(e.target.value as "title" | "date" | "size")}>
            <option value="date">Sort: date</option>
            <option value="title">Sort: title</option>
            <option value="size">Sort: size</option>
          </select>
          <select className="select" style={{ width: "auto" }} value={groupBy} onChange={e => setGroupBy(e.target.value as "none" | "author" | "status")}>
            <option value="none">Group: none</option>
            <option value="author">Group: author</option>
            <option value="status">Group: status</option>
          </select>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => void loadBooks()}>REFRESH</button>
        </div>
      </div>

      {error && <ErrorBanner message={error} onRetry={() => void loadBooks()} />}
      {loading && <Skeleton height={72} count={5} />}
      {!loading && !error && books.length === 0 && (
        <EmptyState title="NO BOOKS MATCH" detail="Try clearing filters or search." />
      )}

      {!loading && groupBy === "none" && renderList(books)}
      {!loading && groupBy === "author" && Object.entries(grouped).map(([author, list]) => (
        <div key={author} style={{ marginBottom: 22 }}>
          <div className="section-label">{author} <span className="muted">({list.length})</span></div>
          {renderList(list)}
        </div>
      ))}
      {!loading && groupBy === "status" && Object.entries(groupedByStatus).map(([st, list]) => (
        <div key={st} style={{ marginBottom: 22 }}>
          <div className="section-label">{st.toUpperCase()} <span className="muted">({list.length})</span></div>
          {renderList(list)}
        </div>
      ))}

      {!loading && total > LIMIT && (
        <div style={{ display: "flex", justifyContent: "center", gap: 12, marginTop: 20 }}>
          <button type="button" className="btn btn-sm btn-ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>PREVIOUS</button>
          <span className="muted" style={{ alignSelf: "center" }}>
            {offset + 1}–{Math.min(offset + LIMIT, total)} of {total}
          </span>
          <button type="button" className="btn btn-sm btn-ghost" disabled={offset + LIMIT >= total} onClick={() => setOffset(offset + LIMIT)}>NEXT</button>
        </div>
      )}
    </div>
  )
}
