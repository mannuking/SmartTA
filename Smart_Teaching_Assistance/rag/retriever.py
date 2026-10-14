"""High-level RAG retriever -- the public API that steps 2-4 call.

Wraps rag/store.py with:
  - Top-K retrieval from Chroma
  - Optional section_id boost
  - Cross-encoder reranking (BGE-reranker-v2-m3)
  - Joint text + figure retrieval

This is the contract the rest of the SmartTA app codes against:

    from Smart_Teaching_Assistance.rag.retriever import retrieve

    context = retrieve(
        corpus, courses_dir,
        query="Explain backpropagation with respect to a concrete example",
        section_id="T3.2.1",       # optional
        k_text=8, k_figures=3,
        rerank=True,
    )
    # context.text_excerpts    -> [{id, text, page, section_id, score}, ...]
    # context.matching_figures -> [{id, image_path, caption, page, score}, ...]
    # context.source_sections  -> ["T3.2.1", "T3.2.3", ...]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..pdf_extractor.corpus import CourseCorpus
from . import store as _store


logger = logging.getLogger(__name__)


@dataclass
class TextExcerpt:
    id: str
    text: str
    page: int
    section_id: str
    score: float = 0.0           # higher is better; negative of distance after rerank

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "page": self.page,
            "section_id": self.section_id,
            "score": self.score,
        }


@dataclass
class FigureHit:
    id: str
    image_path: str
    caption: str
    page: int
    section_id: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "image_path": self.image_path,
            "caption": self.caption,
            "page": self.page,
            "section_id": self.section_id,
            "score": self.score,
        }


@dataclass
class RetrievalContext:
    query: str
    section_id: str = ""
    text_excerpts: list[TextExcerpt] = field(default_factory=list)
    matching_figures: list[FigureHit] = field(default_factory=list)
    source_sections: list[str] = field(default_factory=list)
    used_rerank: bool = False

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "section_id": self.section_id,
            "text_excerpts": [t.to_dict() for t in self.text_excerpts],
            "matching_figures": [f.to_dict() for f in self.matching_figures],
            "source_sections": list(self.source_sections),
            "used_rerank": self.used_rerank,
        }

    def as_prompt_block(self) -> str:
        """Render as a self-contained string to insert into an LLM prompt."""
        parts: list[str] = []
        if self.text_excerpts:
            parts.append("## Retrieved text excerpts from the book")
            for t in self.text_excerpts:
                header = f"[{t.section_id or 'ungrouped'} - p.{t.page+1}]"
                parts.append(f"{header}\n{t.text}")
        if self.matching_figures:
            parts.append("")
            parts.append("## Retrieved figures from the book")
            for f in self.matching_figures:
                header = f"[{f.section_id or 'ungrouped'} - p.{f.page+1}]"
                parts.append(f"{header} (id={f.id}, file={f.image_path})")
                parts.append(f"Caption: {f.caption}")
        return "\n\n".join(parts)


# ── Public API ──────────────────────────────────────────────────────

def retrieve(
    corpus: CourseCorpus,
    courses_dir: str,
    query: str,
    *,
    section_id: str = "",
    k_text: int = 8,
    k_figures: int = 3,
    rerank: bool = True,
    rerank_top_n: int = 20,
) -> RetrievalContext:
    """Retrieve + rerank text + figures for `query`.

    Args:
        corpus: the CourseCorpus to query.
        courses_dir: where the Chroma store lives (same as ingest's courses_dir).
        query: natural-language query.
        section_id: optional, e.g. "T3.2.1". Boosts retrieval toward
            text chunks in the same chapter/section.
        k_text: how many text excerpts to return.
        k_figures: how many figures to return.
        rerank: if True, apply cross-encoder reranking to the top text
            candidates (recommended).
        rerank_top_n: how many candidates to fetch before reranking.
            The final k_text comes from the top of the reranked list.

    Returns:
        RetrievalContext with text_excerpts, matching_figures, etc.
    """
    ctx = RetrievalContext(query=query, section_id=section_id)

    # Make sure the corpus is indexed (no-op if already done)
    _store.index_corpus(corpus, courses_dir)

    # ── Text retrieval ───────────────────────────────────────────
    # Fetch a generous candidate set so reranking has something to work with.
    n_text_initial = max(k_text * 3, rerank_top_n) if rerank else k_text
    text_hits = _store.query_text(
        corpus, courses_dir, query, k=n_text_initial, section_id=section_id or None,
    )
    if not text_hits:
        return ctx

    # ── Text rerank ──────────────────────────────────────────────
    if rerank and len(text_hits) > k_text:
        from .embeddings import rerank as _rerank
        try:
            reranked = _rerank(query, [h["text"] for h in text_hits], top_k=k_text)
            # reranked is a list of (orig_index, score) sorted descending
            for orig_i, score in reranked:
                h = text_hits[orig_i]
                ctx.text_excerpts.append(TextExcerpt(
                    id=h["id"], text=h["text"], page=h["page"],
                    section_id=h["section_id"], score=score,
                ))
            ctx.used_rerank = True
        except Exception as e:
            logger.warning("rerank failed: %s; falling back to raw cosine order", e)
            for h in text_hits[:k_text]:
                ctx.text_excerpts.append(TextExcerpt(
                    id=h["id"], text=h["text"], page=h["page"],
                    section_id=h["section_id"], score=-h["distance"],
                ))
    else:
        for h in text_hits[:k_text]:
            ctx.text_excerpts.append(TextExcerpt(
                id=h["id"], text=h["text"], page=h["page"],
                section_id=h["section_id"], score=-h["distance"],
            ))

    # ── Figure retrieval ─────────────────────────────────────────
    fig_hits = _store.query_figures(corpus, courses_dir, query, k=k_figures)
    for h in fig_hits:
        ctx.matching_figures.append(FigureHit(
            id=h["id"], image_path=h["image_path"], caption=h["caption"],
            page=h["page"], section_id=h["section_id"], score=-h["distance"],
        ))

    # ── Source sections ──────────────────────────────────────────
    sections: list[str] = []
    for t in ctx.text_excerpts:
        if t.section_id and t.section_id not in sections:
            sections.append(t.section_id)
    for f in ctx.matching_figures:
        if f.section_id and f.section_id not in sections:
            sections.append(f.section_id)
    ctx.source_sections = sections

    return ctx


def quick_query(
    corpus: CourseCorpus,
    courses_dir: str,
    query: str,
    *,
    k: int = 5,
) -> RetrievalContext:
    """Convenience: skip reranking, just top-k cosine results."""
    return retrieve(corpus, courses_dir, query, k_text=k, k_figures=2, rerank=False)
