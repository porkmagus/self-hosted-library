import { useState, useEffect, useCallback } from "react"
import type { MediaFile, AudioPlayerState } from "../types"
import { C, ICON } from "../types"
import { AudioPlayer, EmptyState, ErrorBanner, Skeleton } from "../components"
import { formatSize } from "../lib/format"
import { api } from "../lib/api"
import { useDebouncedValue } from "../hooks"

export function MediaPage() {
  const [files, setFiles] = useState<MediaFile[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mediaType, setMediaType] = useState("all")
  const [offset, setOffset] = useState(0)
  const [searchQ, setSearchQ] = useState("")
  const debouncedSearch = useDebouncedValue(searchQ, 250)
  const [player, setPlayer] = useState<AudioPlayerState | null>(null)
  const [selectedVideo, setSelectedVideo] = useState<MediaFile | null>(null)
  const [selectedMediaImage, setSelectedMediaImage] = useState<string | null>(null)
  const LIMIT = 300

  const loadMedia = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        type: mediaType,
        limit: String(LIMIT),
        offset: String(offset),
      })
      const data = await api.mediaList(params)
      const filtered = debouncedSearch
        ? data.files.filter(f =>
            f.name.toLowerCase().includes(debouncedSearch.toLowerCase())
            || f.directory.toLowerCase().includes(debouncedSearch.toLowerCase()))
        : data.files
      setFiles(filtered)
      setTotal(data.total)
    } catch (e) {
      console.error(e)
      setError(e instanceof Error ? e.message : String(e))
    }
    setLoading(false)
  }, [mediaType, offset, debouncedSearch])

  useEffect(() => { setOffset(0) }, [mediaType])
  useEffect(() => { void loadMedia() }, [loadMedia])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (selectedVideo) setSelectedVideo(null)
        else if (selectedMediaImage) setSelectedMediaImage(null)
        else if (player) setPlayer(null)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [selectedVideo, selectedMediaImage, player])

  const playAudio = (file: MediaFile) => {
    setPlayer({
      file,
      src: api.mediaStreamUrl(file.path),
      title: file.name.replace(/\.[^.]+$/, ""),
      isPlaying: true,
    })
  }

  const typeColor = (t: string) => {
    if (t === "audio") return C.gold
    if (t === "video") return C.info
    if (t === "image") return C.success
    if (t === "archive") return "#f09060"
    return C.textMuted
  }

  const grouped: Record<string, MediaFile[]> = {}
  files.forEach(f => {
    const k = f.directory || "root"
    if (!grouped[k]) grouped[k] = []
    grouped[k].push(f)
  })

  if (selectedVideo) {
    return (
      <div className="overlay" style={{ display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 12 }} onClick={() => setSelectedVideo(null)}>
        <div style={{ display: "flex", justifyContent: "space-between", width: "min(80vw, 960px)", padding: "0 4px" }}>
          <span style={{ color: C.gold }}>{selectedVideo.name.replace(/\.[^.]+$/, "")}</span>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSelectedVideo(null)}>✕</button>
        </div>
        <video
          controls
          autoPlay
          src={api.mediaStreamUrl(selectedVideo.path)}
          style={{ maxWidth: "80vw", maxHeight: "70vh", background: "#000", borderRadius: 8 }}
          onClick={e => e.stopPropagation()}
        />
      </div>
    )
  }

  if (selectedMediaImage) {
    return (
      <div className="overlay" style={{ display: "flex", alignItems: "center", justifyContent: "center" }} onClick={() => setSelectedMediaImage(null)}>
        <img src={api.mediaStreamUrl(selectedMediaImage)} alt="" style={{ maxWidth: "92vw", maxHeight: "90vh", borderRadius: 8 }} />
      </div>
    )
  }

  return (
    <div style={{ paddingBottom: player ? 72 : 0 }}>
      <h2 className="page-title">MEDIA</h2>

      <div className="toolbar">
        <input
          className="input"
          style={{ maxWidth: 360 }}
          placeholder="Search media files…"
          value={searchQ}
          onChange={e => setSearchQ(e.target.value)}
        />
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => void loadMedia()}>REFRESH</button>
      </div>

      <div className="chip-row">
        {(["all", "audio", "video", "image", "archive"] as const).map(t => (
          <button key={t} type="button" className={`chip ${mediaType === t ? "active" : ""}`} onClick={() => setMediaType(t)}>
            {(ICON[t === "all" ? "chevron" : t] || "") + " " + t.toUpperCase()}
          </button>
        ))}
      </div>

      {error && <ErrorBanner message={error} onRetry={() => void loadMedia()} />}
      {loading && <Skeleton height={64} count={5} />}

      {!loading && (
        <div className="muted" style={{ marginBottom: 14 }}>
          <strong style={{ color: C.text }}>{files.length}</strong> files
          {total > files.length && ` (showing ${files.length} of ${total})`}
        </div>
      )}

      {!loading && Object.entries(grouped).map(([dir, dirFiles]) => (
        <div key={dir} style={{ marginBottom: 28 }}>
          <div className="section-label" style={{ borderBottom: `1px solid ${C.border}`, paddingBottom: 8 }}>
            ▸ {dir}
            <span className="muted">({dirFiles.length})</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 10 }}>
            {dirFiles.map(f => (
              <div key={f.path} className="media-item">
                <div style={{
                  width: 40, height: 40, borderRadius: 10,
                  background: `${typeColor(f.type)}18`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 16, color: typeColor(f.type), flexShrink: 0,
                }}>{ICON[f.type] || "?"}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: C.text, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginBottom: 2 }}>
                    {f.name.replace(/\.[^.]+$/, "")}
                  </div>
                  <div className="muted">.{f.extension} · {formatSize(f.size)}</div>
                </div>
                {f.type === "audio" && (
                  <button type="button" className="btn btn-icon btn-sm" onClick={() => playAudio(f)} aria-label="Play">{ICON.play}</button>
                )}
                {f.type === "video" && (
                  <button type="button" className="btn btn-icon btn-sm" style={{ borderColor: C.info, color: C.info }} onClick={() => setSelectedVideo(f)} aria-label="Play video">{ICON.video}</button>
                )}
                {f.type === "image" && (
                  <button type="button" className="btn btn-icon btn-sm" style={{ borderColor: C.success, color: C.success }} onClick={e => { e.stopPropagation(); setSelectedMediaImage(f.path) }} aria-label="View">+</button>
                )}
                {f.type === "archive" && (
                  <a className="btn btn-icon btn-sm" href={api.mediaStreamUrl(f.path)} download style={{ borderColor: "#f09060", color: "#f09060", textDecoration: "none" }} aria-label="Download">{ICON.download}</a>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

      {!loading && !error && files.length === 0 && (
        <EmptyState
          title="NO MEDIA FOUND"
          detail={searchQ ? `Nothing matching "${searchQ}".` : "No non-ingestable media under data/."}
        />
      )}

      {player && <AudioPlayer track={player} onClose={() => setPlayer(null)} />}
    </div>
  )
}
