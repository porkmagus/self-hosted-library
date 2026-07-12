import { sourcesForGroup } from "./searchPresentation"
import type { SearchResult } from "../types"
import { expect, it } from "vitest"

const source = (chunk_id: string, book_id: string): SearchResult => ({
  chunk_id, book_id, book_title: `Book ${book_id}`, page_number: 1, score: 1, text: chunk_id, content_type: "text",
})
it("exposes every source result for a grouped book", () => {
  const results = [source("a1", "a"), source("b1", "b"), source("a2", "a")]
  expect(sourcesForGroup(results, "a").map(item => item.chunk_id)).toEqual(["a1", "a2"])
})
