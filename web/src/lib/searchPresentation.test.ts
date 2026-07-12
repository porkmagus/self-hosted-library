import { sourcesForGroup } from "./searchPresentation"
import type { SearchResult } from "../types"

const source = (chunk_id: string, book_id: string): SearchResult => ({
  chunk_id, book_id, book_title: `Book ${book_id}`, page_number: 1, score: 1, text: chunk_id, content_type: "text",
})
const results = [source("a1", "a"), source("b1", "b"), source("a2", "a")]
const grouped = sourcesForGroup(results, "a")
if (grouped.map(item => item.chunk_id).join(",") !== "a1,a2") {
  throw new Error("Group must expose every source result for that book")
}
