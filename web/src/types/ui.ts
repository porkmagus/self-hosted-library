export type TabId = "search" | "library" | "media" | "upload" | "ingest"

export const TAB_PATH: Record<TabId, string> = {
  search: "/",
  library: "/library",
  media: "/media",
  upload: "/upload",
  ingest: "/ingest",
}

export const PATH_TAB: Record<string, TabId> = {
  "/": "search",
  "/search": "search",
  "/library": "library",
  "/media": "media",
  "/upload": "upload",
  "/ingest": "ingest",
}

export const API = "/api" as const

/** Design tokens — dark navy and gold */
export const C = {
  bg: "#070b14",
  bgLight: "#0d1320",
  bgElevated: "#121a2b",
  card: "#151c2c",
  cardHover: "#1c2540",
  border: "#2a3348",
  borderLight: "#3d4a66",
  gold: "#d4af57",
  goldLight: "#f0d78c",
  goldDim: "#8a7030",
  goldGlow: "rgba(212, 175, 87, 0.22)",
  text: "#ebe3d4",
  textDim: "#b8ad96",
  textMuted: "#6e7390",
  success: "#5ecf6a",
  danger: "#ef5350",
  info: "#6eb6ff",
  violet: "#9b7bff",
} as const

export const SPACE = {
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  5: 24,
  6: 32,
  7: 48,
} as const

export const RADIUS = {
  sm: 4,
  md: 8,
  lg: 12,
  pill: 999,
} as const

export const ICON: Record<string, string> = {
  audio: "\u266A",
  video: "\u25B6",
  image: "\u25C2",
  archive: "\u22A1",
  play: "\u25B6",
  pause: "\u23F8",
  expand: "\u2295",
  collapse: "\u2296",
  chevron: "\u25B8",
  download: "\u2913",
  search: "\u2315",
  close: "\u2715",
}
