import { excerptText, normalizeDisplayText } from "./displayText"

function assertEqual(actual: string, expected: string): void {
  if (actual !== expected) throw new Error(`Expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
}

assertEqual(normalizeDisplayText("M\nA\nG\nI\nC\nI\nA\nN"), "MAGICIAN")
assertEqual(normalizeDisplayText("- salt\n- water"), "- salt\n- water")
assertEqual(normalizeDisplayText("Moon over water\nSilver in night\nQuiet ritual"), "Moon over water\nSilver in night\nQuiet ritual")

const longExcerpt = "word ".repeat(180).trim()
const collapsed = excerptText(longExcerpt, false, 120)
const expanded = excerptText(longExcerpt, true, 120)
if (!collapsed.canExpand || collapsed.text.length >= expanded.text.length || !collapsed.text.endsWith("…")) {
  throw new Error("Collapsed excerpt must be visibly shorter and expandable")
}
if (expanded.text !== longExcerpt || !expanded.canExpand) {
  throw new Error("Expanded excerpt must return the complete source text")
}
