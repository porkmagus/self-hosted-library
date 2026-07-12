import { useEffect } from "react"

type Handler = (e: KeyboardEvent) => void

/** Global hotkeys. Ignore when typing in inputs unless allowInInput. */
export function useHotkeys(
  map: Record<string, Handler>,
  deps: unknown[] = [],
  allowInInput = false,
): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const tag = target?.tagName?.toLowerCase()
      const typing =
        tag === "input" || tag === "textarea" || tag === "select" || target?.isContentEditable
      if (typing && !allowInInput && e.key !== "Escape") return

      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key
      const handler = map[key] || map[e.code]
      if (handler) handler(e)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
