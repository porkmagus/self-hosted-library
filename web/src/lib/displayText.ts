const BULLET = /^(?:[-*•‣▪]|\d+[.)]|[A-Za-z][.)])\s+/

export function normalizeDisplayText(value: string): string {
  const blocks = value.replace(/\r\n?/g, "\n").replace(/\u00a0/g, " ").replace(/\u00ad/g, "").split(/\n\s*\n/)
  return blocks.map(raw => {
    const rawLines = raw.split("\n").filter(line => line.trim())
    const lines = rawLines.map(line => line.trim())
    if (!lines.length) return ""
    const structural = lines.some((line, i) => BULLET.test(line) || /^(#|>|```)/.test(line) || /^( {4}|\t)/.test(rawLines[i])) || lines.filter(line => line.includes("|")).length >= 2
    if (structural) return rawLines.join("\n")
    if (lines.length >= 8 && lines.filter(line => line.length === 1).length / lines.length >= 0.9) return lines.join("")
    const lengths = lines.map(line => line.length).sort((a, b) => a - b)
    const median = lengths[Math.floor(lengths.length / 2)]
    const proseRatio = lines.filter(line => line.split(/\s+/).length >= 5).length / lines.length
    if (lines.length >= 4 && median >= 35 && proseRatio >= 0.7) {
      return lines.slice(1).reduce((joined, line) => joined + (joined.endsWith("-") && /^[a-z]/.test(line) ? "" : " ") + line, lines[0])
    }
    return rawLines.join("\n")
  }).filter(Boolean).join("\n\n").trim()
}


export interface ExcerptView {
  text: string
  canExpand: boolean
}

export function excerptText(value: string, expanded: boolean, maxChars = 600): ExcerptView {
  const normalized = normalizeDisplayText(value)
  const canExpand = normalized.length > maxChars
  if (expanded || !canExpand) return { text: normalized, canExpand }
  const boundary = normalized.lastIndexOf(" ", maxChars)
  const end = boundary >= Math.floor(maxChars * 0.75) ? boundary : maxChars
  return { text: `${normalized.slice(0, end).trimEnd()}…`, canExpand }
}
