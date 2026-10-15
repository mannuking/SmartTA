"""Auto-place PDF figures at the right sub-section of detailed notes.

The user wants the system (not them) to decide which extracted figure
goes in which sub-section of the detailed notes. This module:

  1. Takes a list of `notes` (the per-section notes tree from
     `content_generator/lecture_notes.py`).
  2. Takes the CourseCorpus (figures with their CLIP + bge-m3
     embeddings + section_id metadata).
  3. For each note sub-section:
       - Builds a query from the sub-section title + first 200 chars
       - Asks the figure store for the top-3 most relevant figures
         (CLIP + bge-m3 hybrid)
       - Filters out figures already assigned to a sibling sub-section
       - Inserts the best figure (above a similarity threshold) into
         that sub-section's `elements` as a `figure_block` with the
         original image_path and caption

  4. Returns the modified notes tree.

This is intentionally simple. The matching uses cosine similarity in
the bge-m3 text space, with a hard threshold (~0.28) to avoid forcing
a figure into a section that doesn't really need one.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..pdf_extractor.corpus import CourseCorpus
from . import store as _store


logger = logging.getLogger(__name__)


# Cosine similarity threshold for "this figure belongs here". Below this,
# we don't insert. Tuned conservatively -- false positives are worse than
# missed matches because a wrong figure is jarring in the output.
FIGURE_SIM_THRESHOLD = 0.28


def _sub_section_text(note: dict) -> str:
    """Extract the 'query' for matching a figure to this sub-section.

    The note structure in the codebase is:
        {"id": "T2.1", "title": "DFA basics", "elements": [...]}
    with NO top-level "text" or "body" field. To get the
    section's content for embedding, we have to walk the
    elements list and concatenate the text-bearing ones
    (text/heading/callout).
    """
    title = str(note.get("title") or note.get("id") or "")
    parts: list[str] = []
    body_chars = 0
    for el in note.get("elements", []) or []:
        etype = el.get("type", "")
        if etype in ("text", "heading", "callout", "eli5_suggestion",
                      "instructional_content", "additional_notes"):
            chunk = str(el.get("content", "")).strip()
            if chunk:
                parts.append(chunk)
                body_chars += len(chunk)
                if body_chars >= 400:
                    break
    body = " ".join(parts)
    return f"{title}. {body[:400]}".strip()


def place_figures_in_notes(
    notes: list[dict],
    corpus: CourseCorpus,
    courses_dir: str,
    *,
    threshold: float = FIGURE_SIM_THRESHOLD,
    max_figures_per_section: int = 1,
    k_candidates: int = 3,
) -> list[dict]:
    """Mutate-and-return `notes` with figures placed at matching sub-sections.

    Each note is expected to look like:
        {
            "id": "T1.2.1",
            "title": "Definition and scope of NLP",
            "text": "...",
            "elements": [
                # ... existing text/code/quiz elements ...
            ],
        }

    We append a `figure_block` element at the start of the sub-section
    when a match is found:
        {
            "type": "figure_block",
            "image_path": "...",
            "caption": "...",
            "description": "...",
            "source_id": "fig-p47-c3",
            "page": 47,
        }
    """
    if not corpus.figures:
        return notes
    # Make sure the store is populated
    _store.index_corpus(corpus, courses_dir)

    # Pre-build a section_id -> [figures] map from the corpus so we can
    # give a 1.5x boost to figures that fall in the same section as the
    # current note.
    by_section: dict[str, list] = {}
    for f in corpus.figures:
        if f.section_id:
            by_section.setdefault(f.section_id, []).append(f)

    used_figure_ids: set[str] = set()

    for note in notes:
        elements = note.setdefault("elements", [])
        if not isinstance(elements, list):
            continue
        if any(e.get("type") == "figure_block" for e in elements):
            continue  # already has a figure

        query = _sub_section_text(note)
        if not query.strip():
            continue
        sec_id = note.get("id", "")

        # Get the top-k candidates from the figure store
        hits = _store.query_figures(corpus, courses_dir, query, k=k_candidates)
        # Apply the section boost: if the note has a section_id, prefer
        # figures whose section_id matches.
        def boosted(hit):
            score = -hit["distance"]   # convert distance -> similarity
            if sec_id and hit.get("section_id") and hit["section_id"].startswith(sec_id.rsplit(".", 1)[0]):
                score *= 1.5
            if hit["id"] in used_figure_ids:
                score -= 0.5  # deprioritize already-placed figures
            return score

        hits.sort(key=boosted, reverse=True)
        placed = 0
        for h in hits:
            sim = -h["distance"]
            if sim < threshold:
                continue
            if h["id"] in used_figure_ids:
                continue
            # Find the full Figure object to get the description
            fig_obj = next((f for f in corpus.figures if f.id == h["id"]), None)
            if not fig_obj:
                continue
            elements.insert(0, {
                "type": "figure_block",
                "image_path": fig_obj.image_path,
                "caption": fig_obj.caption or "",
                "description": fig_obj.description or "",
                "source_id": fig_obj.id,
                "page": fig_obj.page,
            })
            used_figure_ids.add(fig_obj.id)
            placed += 1
            if placed >= max_figures_per_section:
                break
        if placed:
            logger.info("placed %d figure(s) in %s", placed, sec_id or note.get("title", "?"))

    return notes


# ── CLI smoke test ──────────────────────────────────────────────────

if __name__ == "__main__":
    # Minimal standalone test: build a fake corpus + notes, call the placer.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from Smart_Teaching_Assistance.pdf_extractor.corpus import Figure, TextChunk, SectionTree

    corpus = CourseCorpus(
        pdf_hash="test",
        pdf_path="/tmp/x.pdf",
        pdf_filename="x.pdf",
        figures=[
            Figure(id="f1", page=10, bbox=(0, 0, 100, 100), image_path="", caption="DFA state diagram", description="A DFA", section_id="T2.1"),
            Figure(id="f2", page=20, bbox=(0, 0, 100, 100), image_path="", caption="Turing machine tape", description="A TM", section_id="T3.1"),
        ],
        text_chunks=[],
    )
    notes = [
        {"id": "T2.1", "title": "Deterministic Finite Automata", "text": "A DFA is a finite state machine that accepts or rejects strings.", "elements": []},
        {"id": "T3.1", "title": "Turing Machines", "text": "A Turing machine is a more powerful model of computation.", "elements": []},
        {"id": "T4.1", "title": "Complexity", "text": "How hard is a problem to solve?", "elements": []},
    ]
    place_figures_in_notes(notes, corpus, courses_dir="/tmp/nope")
    for n in notes:
        figs = [e for e in n["elements"] if e.get("type") == "figure_block"]
        print(f"{n['id']}: {len(figs)} figure(s)")
        for f in figs:
            print(f"   {f['source_id']}: {f['caption']!r}")
