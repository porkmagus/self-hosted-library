import React from "react"
import { C } from "../types"

export function Badge({
  children,
  color = C.gold,
}: {
  children: React.ReactNode
  color?: string
}) {
  return (
    <span style={{
      display: "inline-block",
      padding: "3px 9px",
      fontSize: 10,
      letterSpacing: 0.08,
      textTransform: "uppercase",
      background: `${color}18`,
      color,
      borderRadius: 999,
      border: `1px solid ${color}40`,
      fontFamily: "var(--font-ui)",
      whiteSpace: "nowrap",
    }}>{children}</span>
  )
}
