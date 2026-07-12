import { excerptText, normalizeDisplayText } from "./displayText"
import { describe, expect, it } from "vitest"

describe("display text", () => {
  it("repairs character-per-line extraction without flattening structured text", () => {
    expect(normalizeDisplayText("M\nA\nG\nI\nC\nI\nA\nN")).toBe("MAGICIAN")
    expect(normalizeDisplayText("- salt\n- water")).toBe("- salt\n- water")
    expect(normalizeDisplayText("Moon over water\nSilver in night\nQuiet ritual")).toBe(
      "Moon over water\nSilver in night\nQuiet ritual",
    )
  })

  it("returns a visibly shorter collapsed excerpt", () => {
    const longExcerpt = "word ".repeat(180).trim()
    const collapsed = excerptText(longExcerpt, false, 120)
    const expanded = excerptText(longExcerpt, true, 120)
    expect(collapsed.canExpand).toBe(true)
    expect(collapsed.text.length).toBeLessThan(expanded.text.length)
    expect(collapsed.text.endsWith("…")).toBe(true)
    expect(expanded).toEqual({ text: longExcerpt, canExpand: true })
  })
})
