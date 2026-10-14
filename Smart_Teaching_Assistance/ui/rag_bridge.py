"""Bridge from Streamlit session state to the RAG retriever.

Steps 2-4 (roadmap, lesson plan, detailed notes) all want the same
thing: "given a topic, give me the relevant book excerpts and figures."

This module:
  - Holds the per-session CourseCorpus (loaded once after step 1 ingest)
  - Holds the per-session Chroma index (built lazily on first query)
  - Exposes `get_rag_context(topic, section_id=...)` for steps to call

If the user hasn't uploaded a PDF in step 1, `get_rag_context` returns
an empty context and steps fall back to the syllabus-only prompt.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import streamlit as st

from Smart_Teaching_Assistance.pdf_extractor.corpus import CourseCorpus
from Smart_Teaching_Assistance.rag.retriever import retrieve, RetrievalContext
from Smart_Teaching_Assistance.rag import store as _store


logger = logging.getLogger(__name__)


# Where the per-course Chroma stores live. Created on demand.
COURSES_BASE = Path(__file__).resolve().parents[1] / "data" / "courses"


def _ensure_courses_dir() -> Path:
    COURSES_BASE.mkdir(parents=True, exist_ok=True)
    return COURSES_BASE


def load_corpus_for_session(pdf_hash: str) -> Optional[CourseCorpus]:
    """Try to load a previously-saved corpus from disk into the session.

    Returns the corpus (and stashes it in st.session_state.course_corpus)
    or None if there's no saved corpus for this pdf_hash.
    """
    courses_dir = _ensure_courses_dir()
    corpus = CourseCorpus.load(courses_dir, pdf_hash)
    if corpus is None:
        return None
    st.session_state.course_corpus = corpus
    st.session_state.courses_dir = str(courses_dir)
    st.session_state.rag_ready = False
    return corpus


def save_corpus_for_session(corpus: CourseCorpus) -> str:
    """Persist the corpus in the session and on disk. Returns courses_dir."""
    courses_dir = _ensure_courses_dir()
    corpus.save(courses_dir)
    st.session_state.course_corpus = corpus
    st.session_state.courses_dir = str(courses_dir)
    st.session_state.pdf_hash = corpus.pdf_hash
    st.session_state.rag_ready = False
    return str(courses_dir)


def _ensure_indexed() -> bool:
    """Make sure the Chroma store is populated. Returns True on success."""
    corpus = st.session_state.get("course_corpus")
    courses_dir = st.session_state.get("courses_dir")
    if corpus is None or not courses_dir:
        return False
    if st.session_state.get("rag_ready"):
        return True
    try:
        _store.index_corpus(corpus, courses_dir)
        st.session_state.rag_ready = True
        return True
    except Exception as e:
        logger.warning("RAG index failed: %s", e)
        return False


def has_rag() -> bool:
    """True if a PDF was uploaded and a corpus is loaded."""
    return st.session_state.get("course_corpus") is not None


def get_rag_context(
    topic: str,
    *,
    section_id: str = "",
    k_text: int = 6,
    k_figures: int = 2,
    rerank: bool = True,
) -> RetrievalContext:
    """Retrieve text + figure excerpts for `topic`.

    Returns an empty RetrievalContext if RAG is not ready.
    """
    if not has_rag():
        return RetrievalContext(query=topic)
    if not _ensure_indexed():
        return RetrievalContext(query=topic)
    corpus = st.session_state.course_corpus
    courses_dir = st.session_state.courses_dir
    try:
        return retrieve(
            corpus, courses_dir,
            query=topic,
            section_id=section_id,
            k_text=k_text,
            k_figures=k_figures,
            rerank=rerank,
        )
    except Exception as e:
        logger.warning("rag retrieve failed: %s", e)
        return RetrievalContext(query=topic)


def rag_stats() -> dict:
    """Return {text_count, figure_count} for the sidebar display."""
    if not has_rag():
        return {"text_count": 0, "figure_count": 0}
    if not _ensure_indexed():
        return {"text_count": 0, "figure_count": 0}
    corpus = st.session_state.course_corpus
    courses_dir = st.session_state.courses_dir
    return _store.collection_stats(corpus, courses_dir)


def place_figures_into_notes(notes: list[dict]) -> list[dict]:
    """Convenience: figure_placer.place_figures_in_notes wrapped for session use."""
    if not has_rag():
        return notes
    if not _ensure_indexed():
        return notes
    from .rag.figure_placer import place_figures_in_notes as _placer
    corpus = st.session_state.course_corpus
    courses_dir = st.session_state.courses_dir
    try:
        return _placer(notes, corpus, courses_dir)
    except Exception as e:
        logger.warning("figure placement failed: %s", e)
        return notes
