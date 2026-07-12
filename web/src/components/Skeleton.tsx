import { C } from "../types"

export function Skeleton({ height = 80, count = 3 }: { height?: number; count?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, animation: "fadeIn 0.2s ease" }}>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          style={{
            height,
            borderRadius: 10,
            border: `1px solid ${C.border}`,
            background: `linear-gradient(90deg, ${C.card} 20%, ${C.cardHover} 50%, ${C.card} 80%)`,
            backgroundSize: "400px 100%",
            animation: "shimmer 1.25s ease-in-out infinite",
            opacity: 1 - i * 0.08,
          }}
        />
      ))}
    </div>
  )
}
