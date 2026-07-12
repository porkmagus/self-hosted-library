import { C } from "../types"

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div style={{
      background: `linear-gradient(90deg, ${C.danger}22, ${C.danger}10)`,
      border: `1px solid ${C.danger}55`,
      color: C.text,
      padding: "12px 16px",
      borderRadius: 10,
      marginBottom: 16,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: 12,
      animation: "fadeIn 0.2s ease",
    }}>
      <div className="text-wrap" style={{ fontSize: 13, color: "#ffb4b0", lineHeight: 1.45 }}>{message}</div>
      {onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry} style={{ borderColor: C.danger, color: C.danger, flexShrink: 0 }}>
          RETRY
        </button>
      )}
    </div>
  )
}
