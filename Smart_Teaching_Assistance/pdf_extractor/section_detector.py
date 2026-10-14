"""PDF section detector -- builds a chapter/section/subsection tree.

Two strategies, used in this order:

1. **PDF outline (TOC)**: most academic PDFs have an embedded outline
   accessible via `doc.get_toc()`. We walk it recursively and assign
   IDs like T1, T1.1, T1.1.1.

2. **Heading detection by font size** (fallback): for PDFs without an
   outline, we render each page and find text spans whose font size is
   larger than the body text. Headings become sections.

The output is a `SectionTree` (from .corpus) that downstream code uses
to tag text chunks and figures with their section_id, so the retriever
can do section-aware lookup.
"""

from __future__ import annotations

import re
from typing import Optional

import fitz  # PyMuPDF


# Reasonable thresholds for "this is a heading" (in points, at 72 dpi render)
HEADING_FONT_SIZE_MIN = 11.0
HEADING_FONT_SIZE_JUMP = 1.5  # must be at least 1.5pt larger than body


def _normalize_title(title: str) -> str:
    """Clean up heading text -- collapse whitespace, strip."""
    if not title:
        return ""
    title = re.sub(r"\s+", " ", title).strip()
    # Strip trailing dots / page numbers like "Chapter 1 Introduction ........... 5"
    title = re.sub(r"\.{3,}\s*\d+\s*$", "", title)
    title = re.sub(r"\s+\d+\s*$", "", title)
    return title.strip()


def detect_sections_from_outline(pdf_path: str) -> Optional[dict]:
    """Try to read the PDF's built-in outline and return a SectionTree.

    Returns None if the PDF has no usable outline (very common with
    scanned PDFs or non-academic sources).
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return None

    try:
        toc = doc.get_toc(simple=False)  # list of [level, title, page, dest?]
    except Exception:
        toc = []
    finally:
        doc.close()

    if not toc or len(toc) < 2:
        return None  # not enough structure to bother

    # Walk the outline tree, assigning IDs as T1, T1.1, T1.1.1, ...
    # (matches the roadmap generator's naming convention downstream).
    # We only consume levels 1-3 (chapter / section / subsection). Anything
    # deeper is flattened to level 3.
    root: dict = {}
    counters: list[int] = [0, 0, 0]  # one counter per level

    for level, title, page, *_ in toc:
        level = min(max(level, 1), 3) - 1  # 0-indexed
        title = _normalize_title(title)
        if not title:
            continue
        # Reset deeper counters
        counters[level] += 1
        for i in range(level + 1, 3):
            counters[i] = 0
        node_id = "T" + ".".join(str(c) for c in counters[: level + 1])
        # Build path to the node
        cur = root
        for i in range(level):
            parent_id = ".".join(str(c) for c in counters[: i + 1])
            cur = cur.setdefault(parent_id, {"title": "", "page_start": 0, "children": {}})
        cur[node_id] = {
            "title": title,
            "page_start": max(1, int(page)),
            "children": {},
        }

    if not root:
        return None
    return root


def detect_sections_by_font(pdf_path: str) -> dict:
    """Fallback: render each page, find text spans with large font, treat as headings.

    This is a best-effort heuristic. For academic PDFs with proper outlines,
    detect_sections_from_outline is much better.
    """
    doc = fitz.open(pdf_path)
    try:
        # First pass: figure out the body-text font size (most common size on page 1)
        body_size = 10.0
        for page_num in range(min(3, len(doc))):
            page = doc[page_num]
            d = page.get_text("dict")
            sizes: list[float] = []
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            sizes.append(float(span.get("size", 10.0)))
            if sizes:
                from statistics import median
                body_size = median(sizes)
                break
        size_threshold = max(HEADING_FONT_SIZE_MIN, body_size + HEADING_FONT_SIZE_JUMP)

        # Second pass: walk pages, collect headings
        headings: list[tuple[int, str, float]] = []  # (page, title, font_size)
        for page_num in range(len(doc)):
            page = doc[page_num]
            d = page.get_text("dict")
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        size = float(span.get("size", 10.0))
                        if size >= size_threshold and 5 < len(text) < 200:
                            headings.append((page_num + 1, text, size))

        # Group headings into a tree by font size
        # We assume level-1 = largest, level-2 = medium, level-3 = smallest
        unique_sizes = sorted({h[2] for h in headings}, reverse=True)
        size_to_level = {s: i + 1 for i, s in enumerate(unique_sizes[:3])}

        root: dict = {}
        counters: list[int] = [0, 0, 0]
        for page, title, size in headings:
            title = _normalize_title(title)
            if not title:
                continue
            level = min(size_to_level.get(size, 3), 3) - 1
            counters[level] += 1
            for i in range(level + 1, 3):
                counters[i] = 0
            node_id = "T" + ".".join(str(c) for c in counters[: level + 1])
            cur = root
            for i in range(level):
                parent_id = ".".join(str(c) for c in counters[: i + 1])
                cur = cur.setdefault(parent_id, {"title": "", "page_start": 0, "children": {}})
            cur[node_id] = {
                "title": title,
                "page_start": page,
                "children": {},
            }
        return root
    finally:
        doc.close()


def build_section_tree(pdf_path: str) -> dict:
    """Build a SectionTree, trying outline first then font-based fallback."""
    tree = detect_sections_from_outline(pdf_path)
    if tree:
        return tree
    return detect_sections_by_font(pdf_path)


def find_section_for_page(tree: dict, page_num_1based: int) -> str:
    """Given a 1-based page number, return the most-specific section_id it falls in.

    Walks the tree depth-first, picking the deepest section whose page_start
    is <= page_num. If the page is before any section, returns ''.
    """
    best: str = ""
    best_page: int = 0

    def walk(node_id: str, node: dict):
        nonlocal best, best_page
        ps = node.get("page_start", 0)
        if ps <= page_num_1based and ps >= best_page:
            best = node_id
            best_page = ps
        for child_id, child in node.get("children", {}).items():
            walk(child_id, child)

    for root_id, root in tree.items():
        walk(root_id, root)
    return best
