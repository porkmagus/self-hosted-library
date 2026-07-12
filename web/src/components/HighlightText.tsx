import { C } from "../types"
import { normalizeDisplayText } from "../lib/displayText"

export function HighlightText({ text }: { text: string }) {
  const parts = normalizeDisplayText(text).split(/\[>>>(.+?)<<<\]/g)
  return (
    <span>
      {parts.map((part, i) => {
        if (i % 2 === 1) {
          return (
            <span key={i} style={{
              background: `${C.gold}22`, color: C.goldLight,
              padding: "0 2px", borderRadius: 2,
            }}>{part}</span>
          )
        }
        return <span key={i}>{part}</span>
      })}
    </span>
  )
}
