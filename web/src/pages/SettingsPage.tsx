import { useCallback, useEffect, useState } from "react"
import { apiGet, apiPut } from "../lib/api"

const DEFAULTS = {
  app_name: "Self-Hosted Library",
  app_subtitle: "Private document search and research",
  accent_color: "#D4AF57",
}

type Settings = { app_name: string; app_subtitle: string; accent_color: string }

export function SettingsPage() {
  const [settings, setSettings] = useState<Settings>({ ...DEFAULTS })
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = (await apiGet("/settings")) as Record<string, string>
      setSettings({
        app_name: data.app_name ?? DEFAULTS.app_name,
        app_subtitle: data.app_subtitle ?? DEFAULTS.app_subtitle,
        accent_color: data.accent_color ?? DEFAULTS.accent_color,
      })
    } catch {
      // keep defaults if backend is unavailable
    }
  }, [])

  useEffect(() => { load() }, [load])

  function update(field: keyof Settings, value: string) {
    setSettings(prev => ({ ...prev, [field]: value }))
    setDirty(true)
    setToast(null)
    setError(null)
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const updated = (await apiPut("/settings", settings)) as Settings
      setSettings(updated)
      setDirty(false)
      setToast("Settings saved")
      window.dispatchEvent(new CustomEvent("settings-updated"))
      setTimeout(() => setToast(null), 2500)
    } catch (e) {
      setError((e instanceof Error && e.message) || "Save failed")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h2 className="page-title">SETTINGS</h2>
      {toast && (
        <div style={{ padding: "10px 16px", marginBottom: "18px", borderRadius: "8px", background: "rgba(212,175,87,0.12)", border: "1px solid rgba(212,175,87,0.3)", fontSize: "13px", color: "var(--gold)" }}>
          {toast}
        </div>
      )}
      {error && (
        <div style={{ padding: "10px 16px", marginBottom: "18px", borderRadius: "8px", background: "rgba(220,60,60,0.12)", border: "1px solid rgba(220,60,60,0.3)", fontSize: "13px", color: "#e88" }}>
          {error}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "20px", maxWidth: "480px" }}>
        {/* App Name */}
        <div className="card">
          <div className="section-label">APP NAME</div>
          <input
            className="input"
            value={settings.app_name}
            onChange={e => update("app_name", e.target.value)}
          />
        </div>

        {/* Subtitle */}
        <div className="card">
          <div className="section-label">SUBTITLE</div>
          <input
            className="input"
            value={settings.app_subtitle}
            onChange={e => update("app_subtitle", e.target.value)}
          />
        </div>

        {/* Accent Color */}
        <div className="card">
          <div className="section-label">ACCENT COLOR</div>
          <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
            <input
              type="color"
              value={settings.accent_color}
              onChange={e => update("accent_color", e.target.value)}
              style={{ width: "48px", height: "48px", border: "none", cursor: "pointer", background: "transparent", padding: 0 }}
            />
            <span style={{ fontFamily: "ui-monospace, monospace", color: "var(--text-dim)", fontSize: "14px" }}>
              {settings.accent_color}
            </span>
          </div>
        </div>

        {/* Save Button */}
        <button className="btn" disabled={!dirty || saving} onClick={handleSave}>
          {saving ? "SAVING..." : dirty ? "SAVE CHANGES" : "NO CHANGES"}
        </button>

        <p className="muted" style={{ fontSize: "12px", marginTop: "4px" }}>
          Changes apply to the header and UI accent color. Title updates across all pages.
        </p>
      </div>
    </div>
  )
}
