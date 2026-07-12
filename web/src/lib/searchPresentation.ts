import type { SearchResult } from "../types"

export function sourcesForGroup(results: SearchResult[], bookId: string): SearchResult[] {
  return results.filter(result => result.book_id === bookId)
}
