import { useCallback, useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import type { SearchResult, ImageResult, SearchGroup, ContextData, SearchResponse } from "../types"
import { C } from "../types"
import { Spinner, Badge, HighlightText, ParagraphText, EmptyState, ErrorBanner, Skeleton } from "../components"
import { api } from "../lib/api"
import { excerptText, normalizeDisplayText } from "../lib/displayText"
import { sourcesForGroup } from "../lib/searchPresentation"
import { useDebouncedValue, useHotkeys } from "../hooks"

function cachePut<T>(cache: Map<string, T>, key: string, value: T): void {
  cache.delete(key)
  cache.set(key, value)
  if (cache.size > 20) cache.delete(cache.keys().next().value as string)
}

function ResultExcerpt({ text, expanded, onToggle }: { text: string; expanded: boolean; onToggle: () => void }) {
  const excerpt = excerptText(text, expanded)
  return (
    <div>
      <div className="text-wrap" style={{ color: C.textDim, fontSize: 14.5, lineHeight: 1.75, whiteSpace: "normal" }}>{excerpt.text}</div>
      {excerpt.canExpand && <button type="button" className="btn btn-sm btn-ghost" style={{ marginTop: 8 }} onClick={onToggle}>{expanded ? "SHORTEN EXCERPT" : "SHOW FULL EXCERPT"}</button>}
    </div>
  )
}

export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const initialQ = params.get("q") || ""
  const [query, setQuery] = useState(initialQ)
  const debouncedQ = useDebouncedValue(query, 600)
  const [results, setResults] = useState<SearchResult[]>([])
  const [images, setImages] = useState<ImageResult[]>([])
  const [groups, setGroups] = useState<SearchGroup[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [total, setTotal] = useState(0)
  const [viewMode, setViewMode] = useState<"results" | "groups" | "grid">("results")
  const [expandedChunkId, setExpandedChunkId] = useState<string | null>(null)
  const [ctxLoadingChunks, setCtxLoadingChunks] = useState<Set<string>>(new Set())
  const [ctxData, setCtxData] = useState<Record<string, ContextData>>({})
  const [selectedImage, setSelectedImage] = useState<string | null>(null)
  const [pdfBook, setPdfBook] = useState<{ id: string; title: string } | null>(null)
  const [showMoreImages, setShowMoreImages] = useState(false)
  const [hasSearched, setHasSearched] = useState(Boolean(initialQ.trim()))
  const [searchHistory, setSearchHistory] = useState<Array<{ query: string }>>([])
  const [showHistory, setShowHistory] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const lastAuto = useRef("")
  const requestRef = useRef<AbortController | null>(null)
  const requestSequence = useRef(0)
  const searchCache = useRef(new Map<string, SearchResponse>())
  const imageCache = useRef(new Map<string, ImageResult[]>())
  const [searchLimit, setSearchLimit] = useState(20)
  const [expandedExcerpts, setExpandedExcerpts] = useState<Set<string>>(new Set())
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())

  const applySearch = useCallback((data: SearchResponse) => {
    setResults(data.results || [])
    setGroups(data.groups || [])
    setTotal(data.total || 0)
  }, [])

  const doSearch = useCallback(async (q: string, limit = 20) => {
    const normalized = q.trim().replace(/\s+/g, " ")
    if (!normalized) return
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    const sequence = ++requestSequence.current
    const key = `${normalized.toLocaleLowerCase()}|${limit}`
    setSearchLimit(limit)
    setLoading(true)
    setError(null)
    setExpandedChunkId(null)
    setExpandedExcerpts(new Set())
    setExpandedGroups(new Set())
    setCtxData({})
    setCtxLoadingChunks(new Set())
    setShowMoreImages(false)
    setHasSearched(true)
    setParams({ q: normalized }, { replace: true })
    try {
      const cached = searchCache.current.get(key)
      const data = cached || await api.search(normalized, limit, controller.signal)
      if (sequence !== requestSequence.current) return
      if (!cached) cachePut(searchCache.current, key, data)
      applySearch(data)
      setLoading(false)

      const imageKey = normalized.toLocaleLowerCase()
      const cachedImages = imageCache.current.get(imageKey)
      if (cachedImages) setImages(cachedImages)
      else {
        setImages([])
        const imageData = await api.searchImages(normalized, 12, controller.signal)
        if (sequence !== requestSequence.current) return
        cachePut(imageCache.current, imageKey, imageData.images || [])
        setImages(imageData.images || [])
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return
      if (sequence === requestSequence.current) {
        console.error(e)
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      if (sequence === requestSequence.current) setLoading(false)
    }
  }, [applySearch, setParams])

  // Debounced auto-search after 2+ chars (Enter still immediate)
  useEffect(() => {
    const q = debouncedQ.trim()
    if (q.length < 3) return
    if (q === lastAuto.current) return
    lastAuto.current = q
    void doSearch(q)
  }, [debouncedQ, doSearch])

  useEffect(() => () => requestRef.current?.abort(), [])

  useEffect(() => {
    if (showHistory && searchHistory.length === 0) {
      void api.searchHistory(10).then(data => setSearchHistory(data.history || [])).catch(() => {})
    }
  }, [showHistory])

  useHotkeys({
    "/": (e) => {
      e.preventDefault()
      inputRef.current?.focus()
      inputRef.current?.select()
    },
    Escape: () => {
      if (pdfBook) setPdfBook(null)
      else if (selectedImage) setSelectedImage(null)
      else if (expandedChunkId) setExpandedChunkId(null)
      else inputRef.current?.blur()
    },
  }, [pdfBook, selectedImage, expandedChunkId])

  const loadCtx = useCallback(async (r: SearchResult) => {
    const wasExpanded = expandedChunkId === r.chunk_id
    setExpandedChunkId(wasExpanded ? null : r.chunk_id)
    if (wasExpanded) return
    setCtxLoadingChunks(prev => new Set(prev).add(r.chunk_id))
    try {
      const data = await api.context({
        chunk_id: r.chunk_id,
        book_id: r.book_id,
        page_number: r.page_number,
        pages_before: 3,
        pages_after: 3,
      })
      setCtxData(prev => ({ ...prev, [r.chunk_id]: data }))
    } catch (e) {
      console.error(e)
    }
    setCtxLoadingChunks(prev => {
      const n = new Set(prev)
      n.delete(r.chunk_id)
      return n
    })
  }, [expandedChunkId])

  const toggleExpand = useCallback((r: SearchResult) => {
    if (expandedChunkId === r.chunk_id) setExpandedChunkId(null)
    else if (!ctxData[r.chunk_id]) void loadCtx(r)
    else setExpandedChunkId(r.chunk_id)
  }, [expandedChunkId, ctxData, loadCtx])

  const renderCtx = useCallback((chunkId: string) => {
    const d = ctxData[chunkId]
    const isLoading = ctxLoadingChunks.has(chunkId)
    if (!d && !isLoading) return null
    return (
      <div className="card" style={{
        borderTopLeftRadius: 0, borderTopRightRadius: 0, marginTop: -12, marginBottom: 16,
        borderTop: "none", animation: "fadeIn 0.25s ease",
      }}>
        <div className="section-label">
          CONTEXT (±3 INDEXED SECTIONS)
          {isLoading && <Spinner size={12} />}
        </div>
        {isLoading && !d && <Skeleton height={56} count={1} />}
        {d?.highlighted && (
          <div style={{ marginBottom: 16, paddingLeft: 12, borderLeft: `3px solid ${C.gold}` }}>
            <div style={{ marginBottom: 8 }}><Badge>Match — section {d.matched_page}</Badge></div>
            <div className="text-wrap" style={{ fontSize: 15, lineHeight: 1.75, color: C.text, whiteSpace: "pre-wrap" }}>
              <HighlightText text={d.highlighted} />
            </div>
          </div>
        )}
        {d && Object.entries(d.surrounding_pages).map(([pg, text]) => (
          <div key={pg} style={{ marginBottom: 12, padding: 12, background: C.bgLight, borderRadius: 8, border: `1px solid ${C.border}` }}>
            <div style={{ marginBottom: 8 }}><Badge color={C.textMuted}>Section {pg}</Badge></div>
            <ParagraphText text={text} />
          </div>
        ))}
      </div>
    )
  }, [ctxData, ctxLoadingChunks])

  const visibleImages = showMoreImages ? images : images.slice(0, 12)

  if (pdfBook) {
    return (
      <div className="overlay" style={{ display: "flex", flexDirection: "column" }} onClick={() => setPdfBook(null)}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "12px 20px", background: C.bgLight, borderBottom: `1px solid ${C.border}`,
        }} onClick={e => e.stopPropagation()}>
          <span style={{ color: C.gold, fontFamily: "var(--font-display)", letterSpacing: 1 }}>{pdfBook.title}</span>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="btn btn-sm" onClick={() => window.open(api.bookPdfUrl(pdfBook.id), "_blank")}>OPEN IN TAB</button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPdfBook(null)}>CLOSE</button>
          </div>
        </div>
        <iframe src={api.bookPdfUrl(pdfBook.id)} title="PDF Viewer" style={{ flex: 1, border: "none", background: "#000" }} />
      </div>
    )
  }

  if (selectedImage) {
    return (
      <div className="overlay" style={{ display: "flex", alignItems: "center", justifyContent: "center" }} onClick={() => setSelectedImage(null)}>
        <img src={selectedImage} alt="" style={{ maxWidth: "92vw", maxHeight: "90vh", borderRadius: 8, boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }} />
      </div>
    )
  }

  return (
    <div>
      <div className="search-bar">
        <input
          ref={inputRef}
          className="input"
          placeholder="Search titles, topics, authors…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onFocus={() => { if (!query.trim()) setShowHistory(true) }}
          onBlur={() => setTimeout(() => setShowHistory(false), 200)}
          onKeyDown={e => {
            if (e.key === "Enter") {
              lastAuto.current = query.trim()
              void doSearch(query)
              setShowHistory(false)
            }
          }}
          aria-label="Search the library"
        />
        <button type="button" className="btn" onClick={() => { lastAuto.current = query.trim(); void doSearch(query); setShowHistory(false) }} disabled={!query.trim()}>
          {loading ? <Spinner size={14} /> : null}
          SEARCH
        </button>
      </div>

      {showHistory && !query.trim() && searchHistory.length > 0 && (
        <div style={{
          background: C.card, border: `1px solid ${C.border}`, borderRadius: 8,
          marginTop: 8, overflow: "hidden",
        }}>
          <div style={{ padding: "8px 14px", fontSize: 11, color: C.textMuted, letterSpacing: 0.08 }}>
            RECENT SEARCHES
          </div>
          {searchHistory.map((h) => (
            <button
              key={h.query}
              type="button"
              style={{
                display: "block", width: "100%", textAlign: "left", padding: "8px 14px",
                background: "transparent", border: "none", cursor: "pointer",
                color: C.text, fontSize: 14, borderBottom: `1px solid ${C.border}`,
              }}
              onMouseDown={e => {
                e.preventDefault()
                setQuery(h.query)
                lastAuto.current = h.query
                void doSearch(h.query)
                setShowHistory(false)
              }}
            >
              {h.query}
            </button>
          ))}
        </div>
      )}

      {error && <ErrorBanner message={error} onRetry={() => void doSearch(query)} />}
      {!loading && !error && !hasSearched && (
        <EmptyState title="SEARCH THE LIBRARY" detail="Type a phrase, topic, title, or author. Results group by document with expandable context and related images." />
      )}
      {!loading && !error && hasSearched && results.length === 0 && (
        <EmptyState title="NO MATCHES" detail="Try a broader phrase, alternate spelling, or fewer modifiers." />
      )}

      {loading && <Skeleton height={92} count={4} />}

      {!loading && results.length > 0 && (
        <div className="toolbar">
          <div className="muted">
            <strong style={{ color: C.text }}>{total}</strong> results
            {images.length > 0 && <span style={{ marginLeft: 14, color: C.gold }}>+ {images.length} images</span>}
            {groups.length > 0 && <span style={{ marginLeft: 14 }}>across {groups.length} books</span>}
          </div>
          <div className="chip-row" style={{ border: "none", margin: 0, gap: 4 }}>
            {(["results", "groups", "grid"] as const).map(m => (
              <button key={m} type="button" className={`chip ${viewMode === m ? "active" : ""}`} onClick={() => setViewMode(m)}>
                {m.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      )}

      {!loading && images.length > 0 && (
        <div style={{ marginBottom: 28 }}>
          <div className="section-label">
            ✦ IMAGE RESULTS
            <span className="muted" style={{ fontWeight: "normal", letterSpacing: 0 }}>({images.length})</span>
          </div>
          <div className="img-grid">
            {visibleImages.map((img, i) => (
              <div key={`${img.image_id}-${i}`} className="img-card" onClick={() => setSelectedImage(img.image_url)}>
                <img src={img.image_url} alt="" loading="lazy" />
                <div style={{ padding: "8px 10px" }}>
                  <div style={{ fontSize: 11, color: C.textDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{img.book_title}</div>
                  <div style={{ fontSize: 10, color: C.textMuted, marginTop: 2 }}>p.{img.page_number} · {img.score.toFixed(3)}</div>
                </div>
              </div>
            ))}
          </div>
          {images.length > 12 && (
            <button type="button" className="btn btn-sm btn-ghost" style={{ marginTop: 12 }} onClick={() => setShowMoreImages(!showMoreImages)}>
              {showMoreImages ? "SHOW LESS" : `SHOW MORE (${images.length - 12})`}
            </button>
          )}
        </div>
      )}

      {!loading && viewMode === "results" && results.map(r => (
        <div key={r.chunk_id} style={{ marginBottom: 14 }}>
          <div className="card">
            <div style={{ color: C.gold, fontSize: 16, marginBottom: 10, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span
                onClick={() => setPdfBook({ id: r.book_id, title: r.book_title })}
                style={{ cursor: "pointer", textDecoration: "underline", textDecorationStyle: "dotted", textDecorationColor: `${C.gold}66`, fontFamily: "var(--font-display)", letterSpacing: 0.04 }}
              >
                {r.book_title}
              </span>
              <Badge>Section {r.page_number}</Badge>
            </div>
            <ResultExcerpt
              text={r.text}
              expanded={expandedExcerpts.has(r.chunk_id)}
              onToggle={() => setExpandedExcerpts(prev => {
                const next = new Set(prev)
                if (next.has(r.chunk_id)) next.delete(r.chunk_id); else next.add(r.chunk_id)
                return next
              })}
            />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 14, paddingTop: 12, borderTop: `1px solid ${C.border}` }}>
              <div className="muted">score {r.score.toFixed(4)}</div>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => toggleExpand(r)}
                disabled={ctxLoadingChunks.has(r.chunk_id)}
              >
                {ctxLoadingChunks.has(r.chunk_id) ? <Spinner size={12} /> : null}
                {expandedChunkId === r.chunk_id ? "COLLAPSE" : "EXPAND"}
              </button>
            </div>
          </div>
          {expandedChunkId === r.chunk_id && renderCtx(r.chunk_id)}
        </div>
      ))}

      {!loading && viewMode === "groups" && groups.map(g => {
        const sources = sourcesForGroup(results, g.book_id)
        const isOpen = expandedGroups.has(g.book_id)
        return (
          <div key={g.book_id} className="card" style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 240 }}>
                <div style={{ color: C.gold, fontFamily: "var(--font-display)", marginBottom: 6 }}>{g.book_title}</div>
                <div className="muted">{sources.length} source {sources.length === 1 ? "match" : "matches"} · sections {g.pages.join(", ")}</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <Badge>top {g.top_score.toFixed(3)}</Badge>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPdfBook({ id: g.book_id, title: g.book_title })}>OPEN SOURCE PDF</button>
                <button type="button" className="btn btn-sm" onClick={() => setExpandedGroups(prev => {
                  const next = new Set(prev)
                  if (next.has(g.book_id)) next.delete(g.book_id); else next.add(g.book_id)
                  return next
                })}>{isOpen ? "HIDE SOURCES" : `SHOW SOURCES (${sources.length})`}</button>
              </div>
            </div>
            {isOpen && (
              <div style={{ marginTop: 14, display: "grid", gap: 12 }}>
                {sources.map(source => (
                  <div key={source.chunk_id}>
                    <div style={{ padding: 12, background: C.bgLight, borderRadius: 8, border: `1px solid ${C.border}` }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
                        <Badge>Section {source.page_number}</Badge>
                        <span className="muted">score {source.score.toFixed(4)}</span>
                      </div>
                      <ResultExcerpt
                        text={source.text}
                        expanded={expandedExcerpts.has(source.chunk_id)}
                        onToggle={() => setExpandedExcerpts(prev => {
                          const next = new Set(prev)
                          if (next.has(source.chunk_id)) next.delete(source.chunk_id); else next.add(source.chunk_id)
                          return next
                        })}
                      />
                      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                        <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPdfBook({ id: source.book_id, title: source.book_title })}>OPEN SOURCE PDF</button>
                        <button type="button" className="btn btn-sm" onClick={() => toggleExpand(source)} disabled={ctxLoadingChunks.has(source.chunk_id)}>
                          {ctxLoadingChunks.has(source.chunk_id) ? <Spinner size={12} /> : null}
                          {expandedChunkId === source.chunk_id ? "COLLAPSE CONTEXT" : "EXPAND CONTEXT"}
                        </button>
                      </div>
                    </div>
                    {expandedChunkId === source.chunk_id && renderCtx(source.chunk_id)}
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}

      {!loading && viewMode === "grid" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 14 }}>
          {results.map(r => {
            const normalized = normalizeDisplayText(r.text)
            return (
              <div key={r.chunk_id} className="card" style={{ breakInside: "avoid", marginBottom: 14, cursor: "pointer" }} onClick={() => toggleExpand(r)}>
                <div style={{ color: C.gold, fontSize: 13, marginBottom: 8, fontFamily: "var(--font-display)" }}>{r.book_title}</div>
                <div className="text-wrap" style={{ color: C.textDim, fontSize: 13, lineHeight: 1.6 }}>{normalized.slice(0, 280)}{normalized.length > 280 ? "…" : ""}</div>
                <div className="muted" style={{ marginTop: 8 }}>sec.{r.page_number} · {r.score.toFixed(3)}</div>
              </div>
            )
          })}
        </div>
      )}

      {!loading && results.length >= searchLimit && searchLimit < 50 && (
        <div style={{ textAlign: "center", marginTop: 20 }}>
          <button type="button" className="btn" onClick={() => void doSearch(query, Math.min(50, searchLimit + 20))}>LOAD MORE RESULTS</button>
        </div>
      )}
    </div>
  )
}
