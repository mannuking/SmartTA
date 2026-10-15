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

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import streamlit as st

from Smart_Teaching_Assistance.pdf_extractor.corpus import CourseCorpus
from Smart_Teaching_Assistance.rag.retriever import retrieve, RetrievalContext
from Smart_Teaching_Assistance.rag import store as _store


logger = logging.getLogger(__name__)


# ── Per-user, per-course persistent storage ────────────────────────────
#
# SmartTA is a local app: each user has their own data folder on disk
# (per the "no cloud account, isolated install" expectation). The
# layout is:
#
#   <userdata_root>/users/<user_id>/courses/<course_id>/
#       figures/                # all PNG crops extracted from the PDF
#       <pdf_hash>/             # corpus + chroma + figures for this book
#       meta.json               # {course_name, subject, pdf_hash, ...}
#       figures_index.json      # [{id, image_path, caption, ...}, ...]
#
# We default <userdata_root> to %LOCALAPPDATA%/SmartTA (Windows) or
# ~/.local/share/SmartTA (Linux/macOS). The env var SMARTTA_USERDATA
# overrides it for advanced users / portable installs.
def _userdata_root() -> Path:
    env = os.environ.get("SMARTTA_USERDATA")
    if env:
        return Path(env).expanduser().resolve()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "SmartTA"
    # POSIX: XDG_DATA_HOME or ~/.local/share
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "smartta"
    return Path.home() / ".local" / "share" / "smartta"


def _user_id_str() -> str:
    """Return a stable, filesystem-safe identifier for the current user.

    Falls back to a single shared 'default' folder if no user is
    logged in yet (so unauthenticated browsing still has somewhere
    to write temp data)."""
    uid = st.session_state.get("user_id")
    if uid is None:
        return "default"
    return f"user_{uid}"


def _user_root() -> Path:
    """Path to the current user's root folder (auto-created)."""
    p = _userdata_root() / "users" / _user_id_str()
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_courses_base() -> Path:
    """Path to the per-user courses folder. Replaces the old
    flat COURSES_BASE so figures + chroma + corpus are isolated
    per user. Created on demand.
    """
    p = _user_root() / "courses"
    p.mkdir(parents=True, exist_ok=True)
    return p


# Where the per-course Chroma stores live. Created on demand.
# Kept as a module-level name for backward compat (any external
# import still works) but now points at the per-user folder.
COURSES_BASE = user_courses_base()


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
    # ALSO copy the figures into a per-course flat folder for
    # persistent display + export. The corpus already has them at
    # <courses_dir>/<pdf_hash>/figures/ but we want them at a
    # user-visible location that survives the user logging out and
    # back in (or restarting the server).
    try:
        _persist_figures_to_course(corpus, courses_dir)
        save_course_meta(
            pdf_hash=corpus.pdf_hash,
            name=st.session_state.get("subject_name", ""),
            subject=st.session_state.get("subject_name", ""),
            course_id=st.session_state.get("course_id"),
            extra={"pdf_filename": corpus.pdf_filename},
        )
    except Exception as e:
        logger.warning("figure persistence failed: %s", e)
    return str(courses_dir)


def _persist_figures_to_course(corpus: CourseCorpus, courses_dir: Path) -> Path:
    """Copy all figure PNGs from the corpus into a flat
    per-course folder and write a figures_index.json next to them.

    The user-visible folder is:
        <courses_dir>/_figures/<pdf_hash>/figures/*.png
    plus
        <courses_dir>/_figures/<pdf_hash>/figures_index.json

    We keep the original corpus/figures/ as the source of truth
    (chroma reads from there) and just maintain a parallel copy
    for fast list-and-render use in the UI.
    """
    course_figs_dir = courses_dir / "_figures" / corpus.pdf_hash
    figures_dir = course_figs_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    index = []
    for fig in corpus.figures:
        src = Path(fig.image_path) if fig.image_path else None
        if not src or not src.is_file():
            # Skip missing source files but still record the row
            index.append({
                "id": fig.id,
                "page": fig.page,
                "bbox": list(fig.bbox) if fig.bbox else None,
                "image_path": "",
                "caption": fig.caption,
                "description": fig.description,
                "section_id": fig.section_id,
                "content_text": fig.content_text,
            })
            continue
        dst = figures_dir / src.name
        if not dst.is_file():
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                logger.warning("failed to copy figure %s: %s", src, e)
                dst = src  # fall back to original path
        index.append({
            "id": fig.id,
            "page": fig.page,
            "bbox": list(fig.bbox) if fig.bbox else None,
            "image_path": str(dst),
            "caption": fig.caption,
            "description": fig.description,
            "section_id": fig.section_id,
            "content_text": fig.content_text,
        })
    (course_figs_dir / "figures_index.json").write_text(
        json.dumps(index, indent=2, default=str), encoding="utf-8"
    )
    return figures_dir


def save_course_meta(
    pdf_hash: str,
    name: str = "",
    subject: str = "",
    course_id: int = None,
    extra: dict = None,
) -> Path:
    """Write a small meta.json next to the figures so list_courses_for_user
    can find this course even if the SQLite row is gone.
    """
    if not pdf_hash:
        return Path()
    courses_dir = _ensure_courses_dir()
    course_dir = courses_dir / "_figures" / pdf_hash
    course_dir.mkdir(parents=True, exist_ok=True)
    meta_path = course_dir / "meta.json"
    existing = {}
    if meta_path.is_file():
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update({
        "pdf_hash": pdf_hash,
        "name": name or existing.get("name") or "",
        "subject": subject or existing.get("subject") or "",
        "course_id": course_id or existing.get("course_id"),
        "updated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    })
    if extra:
        existing.update(extra)
    meta_path.write_text(
        json.dumps(existing, indent=2, default=str), encoding="utf-8"
    )
    return meta_path


def load_figures_for_course(pdf_hash: str) -> list[dict]:
    """Return the persisted figures_index.json for a course.

    Returns an empty list if the course has no figures or hasn't
    been ingested yet. This is the path the Step 4 / Step 6 UI
    hits to render the 10 figure thumbnails + captions WITHOUT
    needing the in-memory corpus object (so it works after a
    server restart).
    """
    if not pdf_hash:
        return []
    courses_dir = _ensure_courses_dir()
    idx_path = courses_dir / "_figures" / pdf_hash / "figures_index.json"
    if not idx_path.is_file():
        return []
    try:
        return json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("figures_index.json unreadable: %s", e)
        return []


def save_notes_for_course(
    pdf_hash: str,
    all_notes_data: list[dict],
    answer_key_text: str = "",
    notes_markdown: str = "",
) -> Path:
    """Persist the generated notes + answer key + markdown next to
    the figures so the user can skip Step 4 entirely on re-entry.

    Layout: <courses_dir>/_figures/<pdf_hash>/notes.json
    """
    if not pdf_hash or not all_notes_data:
        return Path()
    courses_dir = _ensure_courses_dir()
    course_dir = courses_dir / "_figures" / pdf_hash
    course_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "pdf_hash": pdf_hash,
        "saved_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "all_notes_data": all_notes_data,
        "answer_key_text": answer_key_text,
        "notes_markdown": notes_markdown,
    }
    notes_path = course_dir / "notes.json"
    notes_path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Persisted %d notes to %s", len(all_notes_data), notes_path)
    return notes_path


def load_notes_for_course(pdf_hash: str) -> dict:
    """Load the persisted notes (if any) for a course.

    Returns {"all_notes_data": [...], "answer_key_text": ...,
    "notes_markdown": ...} or {} if the course has no persisted
    notes yet.
    """
    if not pdf_hash:
        return {}
    courses_dir = _ensure_courses_dir()
    notes_path = courses_dir / "_figures" / pdf_hash / "notes.json"
    if not notes_path.is_file():
        return {}
    try:
        return json.loads(notes_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("notes.json unreadable: %s", e)
        return {}


def list_courses_for_user() -> list[dict]:
    """List all courses saved on disk for the current user.

    Each entry: {course_id, course_dir, pdf_hash, name, mtime,
    n_figures, has_notes}. Used by the Dashboard to show "your
    saved courses" + the "Re-enter Step 4" button.
    """
    base = user_courses_base()
    out = []
    for course_dir in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not course_dir.is_dir():
            continue
        meta_path = course_dir / "meta.json"
        meta = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        n_figs = 0
        if (course_dir / "figures").is_dir():
            n_figs = sum(1 for _ in (course_dir / "figures").iterdir() if _.suffix.lower() == ".png")
        # Find any pdf_hash subfolder
        pdf_hash = meta.get("pdf_hash") or ""
        if not pdf_hash:
            for sub in course_dir.iterdir():
                if sub.is_dir() and sub.name not in ("figures", "_figures", "chroma"):
                    pdf_hash = sub.name
                    break
        out.append({
            "course_dir": str(course_dir),
            "course_id": course_dir.name,
            "name": meta.get("name") or course_dir.name,
            "subject": meta.get("subject") or "",
            "pdf_hash": pdf_hash,
            "mtime": course_dir.stat().st_mtime,
            "n_figures": n_figs,
            "has_notes": (course_dir / "notes.json").is_file(),
            "meta": meta,
        })
    return out


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
    section_id: Optional[str] = None,
    k_text: int = 8,
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
    return get_rag_context_for_thread(
        topic,
        corpus=st.session_state.course_corpus,
        courses_dir=st.session_state.courses_dir,
        section_id=section_id,
        k_text=k_text,
        k_figures=k_figures,
        rerank=rerank,
    )


def get_rag_context_for_thread(
    topic: str,
    *,
    corpus,
    courses_dir,
    section_id: Optional[str] = None,
    k_text: int = 8,
    k_figures: int = 2,
    rerank: bool = True,
) -> RetrievalContext:
    """Thread-safe variant of get_rag_context.

    Takes the corpus + courses_dir as explicit arguments instead of
    pulling them from st.session_state (which is unavailable from
    background threads). The first call from the main thread should
    ensure the index is built; subsequent calls in the background
    thread can use the existing on-disk Chroma store.
    """
    if corpus is None or not courses_dir:
        return RetrievalContext(query=topic)
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
    from Smart_Teaching_Assistance.rag.figure_placer import place_figures_in_notes as _placer
    corpus = st.session_state.course_corpus
    courses_dir = st.session_state.courses_dir
    try:
        return _placer(notes, corpus, courses_dir)
    except Exception as e:
        logger.warning("figure placement failed: %s", e)
        return notes
