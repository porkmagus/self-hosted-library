export interface SearchResult {
  text: string
  book_id: string
  book_title: string
  chunk_id: string
  page_number: number
  score: number
  content_type: string
}

export interface ImageResult {
  image_id: string
  book_id: string
  book_title: string
  image_url: string
  page_number: number
  score: number
  content_type: string
}

export interface SearchGroupMatch {
  chunk_id: string
  page_number: number
  text: string
  score: number
}

export interface SearchGroup {
  book_id: string
  book_title: string
  match_count: number
  top_score: number
  pages: number[]
  matches: SearchGroupMatch[]
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
  images: ImageResult[]
  groups: SearchGroup[]
  total: number
  image_count: number
  reranked: boolean
}

export interface ImageSearchResponse {
  query: string
  images: ImageResult[]
  image_count: number
}

export interface ContextData {
  chunk_id: string
  book_id: string
  matched_page: number
  matched_text: string
  highlighted: string
  surrounding_pages: Record<string, string>
  page_count: number
  total_points: number
  available_pages: number[]
}

export interface Book {
  uuid: string
  title: string
  status: string
  original_filename: string
  total_chunks: number
  indexed_chunks: number
}

export interface BookFull extends Book {
  id: number
  author: string | null
  file_extension: string | null
  file_size_bytes: number | null
  created_at: string | null
}

export interface BookFullResponse {
  books: BookFull[]
  total: number
}

export interface MediaFile {
  name: string
  path: string
  type: string
  extension: string
  size: number
  directory: string
  display_name: string
}

export interface MediaResponse {
  files: MediaFile[]
  total: number
  offset: number
  limit: number
  has_more: boolean
}

export interface AudioPlayerState {
  file: MediaFile
  src: string
  title: string
  isPlaying: boolean
}

export interface IngestProgress {
  status: string
  progress: number
  current: number
  total: number
  error?: string
}

export interface PresignResponse {
  file_id: string
  upload_url: string
}

export interface LocalIngestResponse {
  book_count: number
  task_id: string
}
