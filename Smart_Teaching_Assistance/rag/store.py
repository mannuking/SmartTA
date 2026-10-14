"""Chroma-backed vector store for the SmartTA RAG system.

Two collections per course (per pdf_hash):

  - text_chunks: id, page, section_id, text, embedding (bge-m3 1024d)
  - figures:     id, page, section_id, caption, image_path, embedding
                 (CLIP 512d for image + bge-m3 1024d for caption text)

Both collections live under courses/<pdf_hash>/chroma/. Each course
is its own Chroma persistent client, so a fresh course = fresh store,
no cross-contamination.

Why Chroma:
  - Local (no server), persistent, MIT license
  - Supports metadata filtering (we use it for section_id boost)
  - Supports both cosine and L2 distance
  - One-line `pip install chromadb` (already in our requirements)
  - HNSW index, sub-millisecond retrieval on 10k+ vectors

If Chroma is not installed, the store raises a clear ImportError
with install instructions. We do NOT fall back silently -- the user
needs to know.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from ..pdf_extractor.corpus import CourseCorpus, Figure, TextChunk


logger = logging.getLogger(__name__)


# Module-level Chroma client cache: pdf_hash -> PersistentClient
_clients: dict[str, "chromadb.api.ClientAPI"] = {}


def _get_client(courses_dir: str | Path, pdf_hash: str):
    """Return (and cache) a Chroma persistent client for this course."""
    if pdf_hash in _clients:
        return _clients[pdf_hash]
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError as e:
        raise ImportError(
            "chromadb is not installed. Run:\n"
            "    uv pip install chromadb\n"
            "Then re-run."
        ) from e
    persist_dir = Path(courses_dir) / pdf_hash / "chroma"
    persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(persist_dir),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )
    _clients[pdf_hash] = client
    return client


def _text_collection(client, pdf_hash: str):
    return client.get_or_create_collection(
        name="text_chunks",
        metadata={"hnsw:space": "cosine", "pdf_hash": pdf_hash},
    )


def _figures_collection(client, pdf_hash: str):
    return client.get_or_create_collection(
        name="figures",
        metadata={"hnsw:space": "cosine", "pdf_hash": pdf_hash},
    )


# ── Public API ──────────────────────────────────────────────────────

def index_corpus(
    corpus: CourseCorpus,
    courses_dir: str | Path,
    *,
    show_progress: bool = False,
) -> None:
    """Add all chunks + figures from a CourseCorpus to the Chroma store.

    Idempotent: re-running with the same corpus does NOT add duplicates
    (Chroma upserts by id).
    """
    client = _get_client(courses_dir, corpus.pdf_hash)
    text_col = _text_collection(client, corpus.pdf_hash)
    fig_col = _figures_collection(client, corpus.pdf_hash)

    # ── Text chunks ──────────────────────────────────────────────
    if corpus.text_chunks:
        # We need to embed anything not already embedded
        needs_embed_idx = [
            i for i, c in enumerate(corpus.text_chunks)
            if not c.text_embedding
        ]
        if needs_embed_idx:
            from .embeddings import embed_texts
            texts_to_embed = [corpus.text_chunks[i].text for i in needs_embed_idx]
            new_vecs = embed_texts(texts_to_embed, show_progress=show_progress)
            for i, vec in zip(needs_embed_idx, new_vecs):
                corpus.text_chunks[i].text_embedding = vec

        text_col.upsert(
            ids=[c.id for c in corpus.text_chunks],
            embeddings=[c.text_embedding for c in corpus.text_chunks],
            documents=[c.text for c in corpus.text_chunks],
            metadatas=[
                {
                    "page": c.page,
                    "section_id": c.section_id or "",
                    "pdf_hash": corpus.pdf_hash,
                }
                for c in corpus.text_chunks
            ],
        )
        logger.info("indexed %d text chunks", len(corpus.text_chunks))

    # ── Figures ──────────────────────────────────────────────────
    if corpus.figures:
        # Embed the figure captions with bge-m3
        needs_cap_embed = [
            i for i, f in enumerate(corpus.figures)
            if not f.text_embedding
        ]
        if needs_cap_embed:
            from .embeddings import embed_texts
            cap_texts = [
                (f.caption or f.description or "")[:512] or "figure"
                for i, f in [(i, corpus.figures[i]) for i in needs_cap_embed]
            ]
            new_vecs = embed_texts(cap_texts, show_progress=show_progress)
            for i, vec in zip(needs_cap_embed, new_vecs):
                corpus.figures[i].text_embedding = vec

        # Also embed the images with CLIP (separate column -- we store
        # both embeddings; the retriever uses whichever the query type
        # calls for).
        needs_img_embed = [
            i for i, f in enumerate(corpus.figures)
            if not f.image_embedding and Path(f.image_path).is_file()
        ]
        if needs_img_embed:
            from .embeddings import embed_images
            img_paths = [corpus.figures[i].image_path for i in needs_img_embed]
            new_vecs = embed_images(img_paths, show_progress=show_progress)
            for i, vec in zip(needs_img_embed, new_vecs):
                corpus.figures[i].image_embedding = vec

        fig_col.upsert(
            ids=[f.id for f in corpus.figures],
            embeddings=[f.text_embedding for f in corpus.figures],
            documents=[
                f"{f.caption or ''} {f.description or ''}".strip() or "figure"
                for f in corpus.figures
            ],
            metadatas=[
                {
                    "page": f.page,
                    "section_id": f.section_id or "",
                    "image_path": f.image_path,
                    "caption": f.caption or "",
                    "pdf_hash": corpus.pdf_hash,
                }
                for f in corpus.figures
            ],
        )
        logger.info("indexed %d figures", len(corpus.figures))

    # Save the corpus with the new embeddings attached
    corpus.save(courses_dir)


def query_text(
    corpus: CourseCorpus,
    courses_dir: str | Path,
    query: str,
    *,
    k: int = 8,
    section_id: Optional[str] = None,
) -> list[dict]:
    """Return top-k text chunks for `query`, optionally boosted to `section_id`.

    Each result is {id, text, page, section_id, distance}.
    """
    from .embeddings import embed_texts
    client = _get_client(courses_dir, corpus.pdf_hash)
    text_col = _text_collection(client, corpus.pdf_hash)
    qv = embed_texts([query])[0]
    where = None
    if section_id:
        # Match exact section_id OR a parent section (T1.2.1 boost -> T1, T1.2 also match)
        where = {"section_id": {"$contains": section_id.rsplit(".", 1)[0]}} if "." in section_id else {"section_id": section_id}
    res = text_col.query(
        query_embeddings=[qv],
        n_results=k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    out: list[dict] = []
    if not res or not res.get("ids"):
        return out
    for i, doc_id in enumerate(res["ids"][0]):
        out.append({
            "id": doc_id,
            "text": res["documents"][0][i] if res.get("documents") else "",
            "page": (res["metadatas"][0][i].get("page") if res.get("metadatas") else 0),
            "section_id": (res["metadatas"][0][i].get("section_id", "") if res.get("metadatas") else ""),
            "distance": (res["distances"][0][i] if res.get("distances") else 0.0),
        })
    return out


def query_figures(
    corpus: CourseCorpus,
    courses_dir: str | Path,
    query: str,
    *,
    k: int = 3,
) -> list[dict]:
    """Return top-k figures for `query` (matched by caption text).

    Each result is {id, image_path, caption, page, section_id, distance}.
    """
    from .embeddings import embed_texts
    client = _get_client(courses_dir, corpus.pdf_hash)
    fig_col = _figures_collection(client, corpus.pdf_hash)
    qv = embed_texts([query])[0]
    res = fig_col.query(
        query_embeddings=[qv],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    out: list[dict] = []
    if not res or not res.get("ids"):
        return out
    for i, doc_id in enumerate(res["ids"][0]):
        meta = (res["metadatas"][0][i] if res.get("metadatas") else {}) or {}
        out.append({
            "id": doc_id,
            "image_path": meta.get("image_path", ""),
            "caption": meta.get("caption", ""),
            "page": meta.get("page", 0),
            "section_id": meta.get("section_id", ""),
            "distance": (res["distances"][0][i] if res.get("distances") else 0.0),
        })
    return out


def collection_stats(corpus: CourseCorpus, courses_dir: str | Path) -> dict:
    """Return {text_count, figure_count} for this course's store."""
    client = _get_client(courses_dir, corpus.pdf_hash)
    text_col = _text_collection(client, corpus.pdf_hash)
    fig_col = _figures_collection(client, corpus.pdf_hash)
    return {
        "text_count": text_col.count(),
        "figure_count": fig_col.count(),
    }
