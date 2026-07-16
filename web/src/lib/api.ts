import type {
  BookFullResponse,
  ContextData,
  IngestProgress,
  LocalIngestResponse,
  MediaResponse,
  UploadResponse,
  SearchResponse,
  ImageSearchResponse,
} from "../types"
import { API } from "../types"

export class ApiError extends Error {
  status: number
  body: string
  constructor(status: number, statusText: string, body: string) {
    const snippet = body.replace(/\s+/g, " ").slice(0, 180)
    super(
      status
        ? `${status} ${statusText}${snippet ? ` — ${snippet}` : ""}`
        : statusText || "Request failed",
    )
    this.status = status
    this.body = body
  }
}

async function parseJson<T>(res: Response): Promise<T> {
  const text = await res.text()
  const trimmed = text.trimStart()
  if (!res.ok) {
    throw new ApiError(res.status, res.statusText, text)
  }
  if (res.status === 204 || text === "") {
    return undefined as T
  }
  if (trimmed.startsWith("<")) {
    throw new ApiError(
      res.status,
      "Expected JSON but received HTML (proxy/upstream error)",
      text,
    )
  }
  try {
    return JSON.parse(text) as T
  } catch {
    throw new ApiError(res.status, "Invalid JSON response", text)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers || {}),
    },
  })
  return parseJson<T>(res)
}

export { request as apiRequest }

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { signal })
}

export function apiPut<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body) })
}

export const api = {
  search: (q: string, limit = 20, signal?: AbortSignal) =>
    request<SearchResponse>(`/search?q=${encodeURIComponent(q)}&limit=${limit}`, { signal }),

  searchImages: (q: string, limit = 12, signal?: AbortSignal) =>
    request<ImageSearchResponse>(`/search/images?q=${encodeURIComponent(q)}&limit=${limit}`, { signal }),

  context: (body: {
    chunk_id: string
    book_id: string
    page_number?: number
    pages_before?: number
    pages_after?: number
  }) => request<ContextData>(`/context`, { method: "POST", body: JSON.stringify(body) }),

  books: (params: URLSearchParams) =>
    request<BookFullResponse>(`/books?${params.toString()}`),

  mediaList: (params: URLSearchParams) =>
    request<MediaResponse>(`/media/list?${params.toString()}`),

  mediaStreamUrl: (path: string) =>
    `${API}/media/stream?path=${encodeURIComponent(path)}`,

  bookPdfUrl: (id: string) => `${API}/book/${id}/pdf`,

  ingestStatus: () => request<IngestProgress>(`/ingest/status`),

  ingestProgress: (taskId: string) =>
    request<IngestProgress>(`/ingest/${taskId}/progress`),

  ingestLocal: (scanInbox: boolean) =>
    request<LocalIngestResponse>(`/ingest/local?scan_inbox=${scanInbox}`, {
      method: "POST",
    }),


  searchHistory: (limit = 10, signal?: AbortSignal) =>
    request<{ history: Array<{ query: string }>; total: number }>(
      `/search/history?limit=${limit}`,
      { signal },
    ),

  ingestBooks: (taskId: string) =>
    request<{
      task_id: string
      active: Array<{
        uuid: string
        title: string
        status: string
        total_chunks: number
        indexed_chunks: number
        progress: number
      }>
      failed: Array<{ uuid: string; title: string; error_message: string }>
      active_count: number
      failed_count: number
    }>(`/ingest/${taskId}/books`),

  deleteBook: (bookId: string) =>
    request<{ deleted: boolean; book_id: string }>(`/books/${bookId}`, {
      method: "DELETE",
    }),

  uploadFile: async (file: File, onProgress?: (pct: number) => void) => {
    return await new Promise<UploadResponse>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open("POST", `${API}/upload`)
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable && onProgress) {
          onProgress(Math.round((ev.loaded / ev.total) * 100))
        }
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as UploadResponse)
          } catch {
            reject(new ApiError(xhr.status, "Invalid JSON response", xhr.responseText || ""))
          }
        } else reject(new ApiError(xhr.status, xhr.statusText, xhr.responseText || ""))
      }
      xhr.onerror = () => reject(new Error("Network error during upload"))
      const form = new FormData()
      form.append("file", file)
      xhr.send(form)
    })
  },
}
