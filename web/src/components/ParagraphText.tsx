import { C } from "../types"
import { normalizeDisplayText } from "../lib/displayText"

export function ParagraphText({ text }: { text: string }) {
  const paragraphs = normalizeDisplayText(text).split(/\n\s*\n/).filter(p => p.trim())
  if (paragraphs.length <= 1) {
    return <div className="text-wrap" style={{ whiteSpace: "pre-wrap", lineHeight: 1.7, fontSize: 14, color: C.textDim }}>{normalizeDisplayText(text)}</div>
  }
  return (
    <div>
      {paragraphs.map((p, i) => (
        <p key={i} style={{
          margin: "0 0 10px 0", lineHeight: 1.7,
          whiteSpace: "pre-wrap", fontSize: 14, color: C.textDim,
        }}>{p}</p>
      ))}
    </div>
  )
}
