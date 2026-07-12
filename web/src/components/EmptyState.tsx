import { C } from "../types"

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div style={{
      textAlign: "center",
      padding: "56px 24px",
      borderRadius: 12,
      border: `1px dashed ${C.borderLight}`,
      background: `linear-gradient(180deg, ${C.card}cc, ${C.bgElevated}99)`,
      animation: "fadeIn 0.3s ease",
    }}>
      <div style={{
        width: 48, height: 48, margin: "0 auto 16px",
        borderRadius: "50%",
        border: `1px solid ${C.gold}44`,
        display: "flex", alignItems: "center", justifyContent: "center",
        color: C.gold, fontSize: 20,
        boxShadow: `0 0 24px ${C.goldGlow}`,
      }}>✦</div>
      <div style={{
        fontFamily: "var(--font-display)",
        color: C.gold,
        fontSize: 15,
        letterSpacing: 0.16,
        marginBottom: 10,
      }}>{title}</div>
      {detail && <div style={{ color: C.textMuted, fontSize: 14, maxWidth: 420, margin: "0 auto", lineHeight: 1.6 }}>{detail}</div>}
    </div>
  )
}
