"""Build a minimal exportable notes structure from a roadmap.

Used by Step 6 (Export) when the user clicks "Skip to Export" from
Step 4 and we don't have full detailed notes yet but DO have a
roadmap. We turn the T-numbered roadmap text into a single notes
"topic" so the ExportManager can produce a real PDF / DOCX / MD
file instead of erroring out.

The result is intentionally minimal: one topic per top-level roadmap
item, with the title + a short description pulled from the roadmap
text. Users get a usable file, even if it's not as rich as the
proper detailed-notes generation.
"""

from __future__ import annotations

import re
from typing import List, Dict


# T1, T1.1, T1.2.3, etc.
_TOPIC_RE = re.compile(r"^T\d+(?:\.\d+)*[\.\):\s]+(.+)$", re.MULTILINE)


def _parse_roadmap(roadmap_text: str) -> List[Dict[str, str]]:
    """Parse T-numbered roadmap lines into [{id, title}] entries.

    Falls back to splitting on newlines and stripping if the
    canonical T-numbering isn't present.
    """
    if not roadmap_text:
        return []

    topics: List[Dict[str, str]] = []
    seen_ids: set[str] = set()

    # Walk every T1 / T1.2 / T3.4.5 line
    for m in _TOPIC_RE.finditer(roadmap_text):
        title = m.group(1).strip()
        if not title:
            continue
        # Reconstruct the ID from the match position
        line_start = m.start()
        # The T-number sits at the start of the matched line
        # Re-extract just the id portion
        line_end = m.end()
        prefix_text = roadmap_text[line_start:line_end]
        id_match = re.match(r"^(T\d+(?:\.\d+)*)", prefix_text)
        if not id_match:
            continue
        tid = id_match.group(1)
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        topics.append({"id": tid, "title": title})

    if topics:
        return topics

    # Fallback: split on newlines, take non-empty lines as titles
    for idx, line in enumerate(roadmap_text.splitlines(), start=1):
        line = line.strip(" \t-*#>•")
        if not line or len(line) > 200:
            continue
        topics.append({"id": f"T{idx}", "title": line})
    return topics


def build_notes_from_roadmap(roadmap_text: str, subject: str) -> List[Dict]:
    """Convert roadmap text into the all_notes_data structure that
    ExportManager expects.

    Returns a list of topic dicts in the shape:
      {"id": "T1", "title": "...", "elements": [{"type": ..., "content": ...}]}

    The export engine consumes this list; it will produce a real PDF
    / DOCX even though the content is just the roadmap outline.
    """
    topics = _parse_roadmap(roadmap_text)
    if not topics:
        # Absolute fallback: one topic with the raw roadmap as content
        return [{
            "id": "T0",
            "title": subject or "Course Outline",
            "elements": [
                {"type": "heading", "level": 1, "content": subject or "Course Outline"},
                {"type": "text", "content": roadmap_text or "(empty roadmap)"},
            ],
        }]

    notes: List[Dict] = []
    # First topic gets a top-level heading with the subject name
    for i, t in enumerate(topics):
        elements: List[Dict] = []
        if i == 0:
            elements.append({
                "type": "heading",
                "level": 1,
                "content": subject or t["title"],
            })
        elements.append({
            "type": "heading",
            "level": 2,
            "content": f"{t['id']} — {t['title']}",
        })
        # A short callout so the file clearly says "this is a quick
        # export, not the full detailed notes"
        if i == 0:
            elements.append({
                "type": "callout",
                "content": (
                    "Quick export generated from your roadmap. For full "
                    "detailed notes (definitions, worked examples, "
                    "exercises), go back to **Step 4 (Detailed Notes)** "
                    "and click \"Generate Notes\"."
                ),
            })
        notes.append({
            "id": t["id"],
            "title": t["title"],
            "elements": elements,
        })
    return notes
