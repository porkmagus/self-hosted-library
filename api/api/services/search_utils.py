"""Pure search helpers shared by retrieval, ingestion, and presentation."""
from __future__ import annotations

import re
from typing import Any

_BULLET = re.compile(r"^(?:[-*•‣▪]|\d+[.)]|[A-Za-z][.)])\s+")


def normalize_query(query: str) -> str:
    return " ".join(query.casefold().split())


def retrieval_limit(limit: int) -> int:
    return min(max(limit * 2, 40), 80)


def normalize_display_text(text: str) -> str:
    """Repair only high-confidence OCR/hard-wrap damage; preserve structured text."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").replace("\u00ad", "")
    blocks = re.split(r"\n\s*\n", text)
    fixed: list[str] = []
    for raw in blocks:
        raw_lines = [line.rstrip() for line in raw.split("\n") if line.strip()]
        lines = [line.strip() for line in raw_lines]
        if not lines:
            continue
        structural = any(
            _BULLET.match(line) or line.startswith(("#", ">", "```")) or raw_line.startswith(("    ", "\t"))
            for raw_line, line in zip(raw_lines, lines, strict=True)
        ) or sum("|" in line for line in lines) >= 2
        if structural:
            fixed.append("\n".join(raw_lines))
            continue
        if len(lines) >= 8 and sum(len(line) == 1 for line in lines) / len(lines) >= 0.9:
            fixed.append("".join(lines))
            continue
        lengths = sorted(len(line) for line in lines)
        median = lengths[len(lengths) // 2]
        prose_lines = sum(len(line.split()) >= 5 for line in lines)
        if len(lines) >= 4 and median >= 35 and prose_lines / len(lines) >= 0.7:
            joined = lines[0]
            for line in lines[1:]:
                joined += ("" if joined.endswith("-") and line[:1].islower() else " ") + line
            fixed.append(joined)
        else:
            fixed.append("\n".join(raw_lines))
    return "\n\n".join(fixed).strip()


def diversify_results(results: list[dict[str, Any]], max_per_book: int = 3) -> list[dict[str, Any]]:
    """Keep ranking mostly intact while preventing one book from owning the first screen."""
    visible: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for result in results:
        book = str(result.get("book_id", ""))
        fingerprint = " ".join(str(result.get("text", "")).casefold().split())[:240]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        if counts.get(book, 0) < max_per_book:
            visible.append(result)
            counts[book] = counts.get(book, 0) + 1
        else:
            deferred.append(result)
    return visible + deferred
