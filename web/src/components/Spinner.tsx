import { C } from "../types"

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: "50%",
      border: `2px solid ${C.border}`,
      borderTopColor: C.gold,
      animation: "spin 0.65s linear infinite",
      display: "inline-block",
      flexShrink: 0,
    }} />
  )
}
