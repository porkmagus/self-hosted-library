import { NavLink, Route, Routes, Navigate } from "react-router-dom"
import { useState, useEffect, useCallback } from "react"
import { GLOBAL_CSS } from "./theme"
import { SearchPage, LibraryPage, MediaPage, UploadPage, IngestPage, SettingsPage } from "./pages"
import { apiGet } from "./lib/api"

type Settings = { app_name: string; app_subtitle: string; accent_color: string }

const DEFAULT_SETTINGS: Settings = {
  app_name: "SELF-HOSTED LIBRARY",
  app_subtitle: "Private document search and research",
  accent_color: "#D4AF57",
}

const tabs = [
  { to: "/", label: "SEARCH", end: true },
  { to: "/library", label: "LIBRARY", end: false },
  { to: "/media", label: "MEDIA", end: false },
  { to: "/upload", label: "UPLOAD", end: false },
  { to: "/ingest", label: "INGEST", end: false },
  { to: "/settings", label: "SETTINGS", end: false },
] as const

// Hex to rgba helper
function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

// Darken / lighten a hex color
function adjustColor(hex: string, amount: number): string {
  let r = parseInt(hex.slice(1, 3), 16)
  let g = parseInt(hex.slice(3, 5), 16)
  let b = parseInt(hex.slice(5, 7), 16)
  r = Math.min(255, Math.max(0, r + amount))
  g = Math.min(255, Math.max(0, g + amount))
  b = Math.min(255, Math.max(0, b + amount))
  return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`
}

export default function App() {
  const [settings, setSettings] = useState<Settings>({ ...DEFAULT_SETTINGS })

  const loadSettings = useCallback(async () => {
    try {
      const data = (await apiGet("/settings")) as Record<string, string>
      setSettings({
        app_name: (data.app_name ?? DEFAULT_SETTINGS.app_name).toUpperCase(),
        app_subtitle: data.app_subtitle ?? DEFAULT_SETTINGS.app_subtitle,
        accent_color: data.accent_color ?? DEFAULT_SETTINGS.accent_color,
      })
    } catch {
      // keep defaults
    }
  }, [])

  useEffect(() => { loadSettings() }, [loadSettings])

  // Listen for settings-updated events from the SettingsPage
  useEffect(() => {
    const handler = () => loadSettings()
    window.addEventListener("settings-updated", handler)
    return () => window.removeEventListener("settings-updated", handler)
  }, [loadSettings])

  const accent = settings.accent_color
  const accentLight = adjustColor(accent, 40)
  const accentGlow = hexToRgba(accent, 0.25)

  // Dynamic CSS overrides for accent color
  const accentOverrides = `
    :root {
      --accent: ${accent};
      --accent-light: ${accentLight};
      --accent-glow: ${accentGlow};
    }
    .app-title {
      color: ${accent} !important;
      text-shadow: 0 0 40px ${accentGlow}, 0 0 2px ${hexToRgba(accent, 0.45)} !important;
    }
    .app-subtitle { color: var(--text-muted); }
    .app-nav a.active {
      color: ${accent} !important;
      background: linear-gradient(180deg, ${hexToRgba(accent, 0.14)}, ${hexToRgba(accent, 0.05)}) !important;
      box-shadow: inset 0 0 0 1px ${hexToRgba(accent, 0.28)} !important;
    }
    .app-nav a:hover { color: ${accentLight} !important; background: ${hexToRgba(accent, 0.06)} !important; }
    .page-title { color: ${accent} !important; }
    .section-label { color: ${accent} !important; }
    .stat-card .value { color: ${accent} !important; }
    .btn {
      border-color: ${accent} !important;
      background: linear-gradient(180deg, ${hexToRgba(accent, 0.12)}, ${hexToRgba(accent, 0.04)}) !important;
      color: ${accent} !important;
    }
    .btn:hover:not(:disabled) {
      background: linear-gradient(180deg, ${hexToRgba(accent, 0.22)}, ${hexToRgba(accent, 0.08)}) !important;
      box-shadow: 0 0 18px ${accentGlow} !important;
      color: ${accentLight} !important;
    }
    .input:focus, .select:focus {
      border-color: ${accent} !important;
      box-shadow: 0 0 0 3px ${accentGlow} !important;
    }
    .app-header::after {
      background: linear-gradient(90deg, transparent, ${accent}55, transparent) !important;
    }
    ::-webkit-scrollbar-thumb {
      background: linear-gradient(180deg, ${hexToRgba(accent, 0.45)}, ${hexToRgba(accent, 0.2)}) !important;
    }
    ::-webkit-scrollbar-thumb:hover { background: ${accent} !important; }
    ::selection { background: ${hexToRgba(accent, 0.45)} !important; }
    .btn:focus-visible, input:focus-visible, select:focus-visible, a:focus-visible {
      outline-color: ${accent} !important;
    }
    .chip.active { color: ${accent} !important; border-bottom-color: ${accent} !important; }
    .chip:hover { color: ${accentLight} !important; }
  `

  return (
    <>
      <style>{GLOBAL_CSS}</style>
      <style>{accentOverrides}</style>
      <div className="app-shell">
        <header className="app-header">
          <h1 className="app-title">{settings.app_name}</h1>
          <p className="app-subtitle">{settings.app_subtitle}</p>
        </header>

        <nav className="app-nav" aria-label="Primary">
          {tabs.map(t => (
            <NavLink
              key={t.to}
              to={t.to}
              end={t.end}
              className={({ isActive }) => (isActive ? "active" : undefined)}
            >
              {t.label}
            </NavLink>
          ))}
        </nav>

        <main style={{ animation: "fadeIn 0.28s ease" }}>
          <Routes>
            <Route path="/" element={<SearchPage />} />
            <Route path="/search" element={<Navigate to="/" replace />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/media" element={<MediaPage />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/ingest" element={<IngestPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>

        <footer className="app-footer-hint">
          <kbd>/</kbd> focus search
          {" · "}
          <kbd>Esc</kbd> close overlay
          {" · "}
          <kbd>Enter</kbd> run search
        </footer>
      </div>
    </>
  )
}
