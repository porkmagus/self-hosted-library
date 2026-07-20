import React, { useEffect, useRef, useState } from "react"
import type { AudioPlayerState } from "../types"
import { C, ICON, API } from "../types"
import { formatDuration } from "../lib/format"

export function AudioPlayer({ track, onClose }: { track: AudioPlayerState; onClose: () => void }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [progress, setProgress] = useState(0)
  const [duration, setDuration] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const onMeta = () => {
      setDuration(audio.duration)
      audio.play().then(() => setIsPlaying(true)).catch(() => undefined)
    }
    const onTime = () => setProgress(audio.currentTime)
    const onEnd = () => setIsPlaying(false)
    audio.addEventListener("loadedmetadata", onMeta)
    audio.addEventListener("timeupdate", onTime)
    audio.addEventListener("ended", onEnd)
    return () => {
      audio.removeEventListener("loadedmetadata", onMeta)
      audio.removeEventListener("timeupdate", onTime)
      audio.removeEventListener("ended", onEnd)
    }
  }, [track.src])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.target !== document.body) return
      e.preventDefault()
      const audio = audioRef.current
      if (!audio) return
      if (audio.paused) {
        void audio.play().then(() => setIsPlaying(true))
      } else {
        audio.pause()
        setIsPlaying(false)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const toggle = async () => {
    if (!audioRef.current) return
    if (isPlaying) {
      audioRef.current.pause()
      setIsPlaying(false)
    } else {
      try {
        await audioRef.current.play()
        setIsPlaying(true)
      } catch {
        /* autoplay blocked or playback error */
      }
    }
  }

  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!audioRef.current || !duration) return
    const el = e.currentTarget as HTMLElement
    const rect = el.getBoundingClientRect()
    const ratio = (e.clientX - rect.left) / rect.width
    audioRef.current.currentTime = ratio * duration
  }

  return (
    <div style={{
      position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 2000,
      background: `linear-gradient(180deg, ${C.bgLight}ee, ${C.bgLight})`,
      borderTop: `1px solid ${C.gold}40`, padding: "8px 20px",
      display: "flex", alignItems: "center", gap: 16,
      backdropFilter: "blur(10px)",
    }}>
      <audio ref={audioRef} src={`${API}/media/stream?path=${encodeURIComponent(track.file.path)}`} />
      <button onClick={toggle} style={{
        background: "none", border: `1px solid ${C.gold}`, color: C.gold,
        width: 36, height: 36, borderRadius: "50%", cursor: "pointer",
        display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, flexShrink: 0,
      }}>{ICON[isPlaying ? "pause" : "play"]}</button>
      <div style={{ minWidth: 140, flexShrink: 0 }}>
        <div style={{ color: C.gold, fontSize: 13, fontWeight: "bold" }}>{track.title}</div>
        <div style={{ color: C.textMuted, fontSize: 11 }}>{track.file.display_name}</div>
      </div>
      <div onClick={seek} style={{
        flex: 1, height: 4, background: C.border, borderRadius: 2, cursor: "pointer",
      }}>
        <div style={{
          height: "100%", background: C.gold, borderRadius: 2,
          width: `${duration ? (progress / duration) * 100 : 0}%`,
          transition: "width 0.2s linear",
        }} />
      </div>
      <span style={{ color: C.textMuted, fontSize: 11, minWidth: 80, textAlign: "right" }}>
        {formatDuration(progress)} / {formatDuration(duration)}
      </span>
      <button onClick={onClose} style={{
        background: "none", border: "none", color: C.textMuted, cursor: "pointer",
        fontSize: 16, padding: "0 4px", flexShrink: 0,
      }}>{"\u2715"}</button>
    </div>
  )
}
