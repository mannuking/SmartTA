"""Step 1: Input Materials — Subject name, syllabus, and optional PDF upload."""

import streamlit as st
import os
import tempfile
from pathlib import Path

from Smart_Teaching_Assistance.ui.steps._shared import (
    get_config, get_db, save_course_state, MAX_BOOK_TOKENS,
    nav_buttons, log_activity, log_error, logger,
)


def _get_pdf_extractor(api_key, db_path, db, user_id=None):
    """Lazy import to avoid crash when fitz/PyMuPDF is not installed."""
    from Smart_Teaching_Assistance.pdf_extractor.extractor import PDFImageExtractor
    return PDFImageExtractor(api_key, db_path, db, user_id=user_id)


def step_input():
    st.markdown(
        "Provide your **subject name**, **syllabus text** (required) "
        "and optionally upload a **textbook PDF** for richer context."
    )

    # ── Subject name ─────────────────────────────────────────────────────
    subj_name = st.text_input(
        "📝 Subject Name",
        value=st.session_state.subject_name,
        placeholder="e.g., Computer Architecture",
        key="subject_name_input",
    )
    if subj_name != st.session_state.subject_name:
        st.session_state.subject_name = subj_name

    # ── Syllabus ─────────────────────────────────────────────────────────
    st.subheader("📋 Syllabus / Course Outline")
    upload_method = st.radio("Input method:", ["Upload File", "Paste Text"], horizontal=True, key="sm")

    if upload_method == "Upload File":
        uploaded_syllabus = st.file_uploader("Choose TXT or PDF", type=["txt", "pdf"])
        if uploaded_syllabus is not None:
            if "pdf" in uploaded_syllabus.type:
                try:
                    from pypdf import PdfReader

                    reader = PdfReader(uploaded_syllabus)
                    text = "".join(p.extract_text() or "" for p in reader.pages)
                    if text.strip():
                        st.session_state.syllabus_text = text
                        log_activity(f"Syllabus PDF loaded ({len(text):,} chars)", icon="📋", level="success")
                except Exception as e:
                    st.error(f"Error: {e}")
                    log_error(str(e))
            else:
                st.session_state.syllabus_text = uploaded_syllabus.read().decode("utf-8")
                log_activity("Syllabus text file loaded", icon="📋", level="success")
    else:
        st.session_state.syllabus_text = st.text_area(
            "Paste syllabus",
            value=st.session_state.syllabus_text,
            height=200,
            key="st_pasted",
        )

    # ── Textbook PDF ─────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📄 Textbook PDF (optional) — extracts figures & enables RAG")
    st.caption(
        "Upload a PDF (textbook, paper, anything). The system extracts the text, "
        "crops every figure, captions each one with AI, builds a section tree, and "
        "indexes everything for retrieval. The roadmap, lesson plan, and detailed "
        "notes steps will then use this as their source of truth."
    )
    uploaded = st.file_uploader("Upload PDF", type=["pdf"], key="pdf_book")

    if uploaded is not None and not st.session_state.pdf_uploaded:
        col1, col2 = st.columns(2)
        with col1:
            do_process = st.button("🚀 Process PDF (full)", type="primary",
                                    help="Text + figure extraction + section tree + RAG index. ~30-60s for a 20pp PDF.")
        with col2:
            do_quick = st.button("⚡ Quick text only",
                                 help="Skip figure extraction; use plain text in prompts. Faster for very large PDFs.")

        if do_process or do_quick:
            # Status container so the user can see real progress
            status = st.status("Starting PDF ingestion…", expanded=True)
            try:
                config = get_config()
                os.makedirs(config.temp_uploads_dir, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".pdf", dir=config.temp_uploads_dir
                ) as tmp:
                    tmp.write(uploaded.getvalue())
                    tmp_path = tmp.name

                if do_process:
                    # Full ingest: text + figures + sections + RAG index
                    from Smart_Teaching_Assistance.pdf_extractor.ingest import ingest_pdf

                    courses_dir = str(
                        Path(__file__).resolve().parents[2] / "data" / "courses"
                    )
                    os.makedirs(courses_dir, exist_ok=True)

                    def _on_progress(done: int, total: int, msg: str):
                        status.update(label=f"📥 {msg}", state="running")
                        status.progress(min(done, 100) / 100.0)

                    corpus = ingest_pdf(
                        tmp_path,
                        courses_dir=courses_dir,
                        progress_cb=_on_progress,
                        force=True,
                    )
                    # Stash in session + on disk
                    from Smart_Teaching_Assistance.ui.rag_bridge import save_corpus_for_session
                    save_corpus_for_session(corpus)

                    st.session_state.pdf_uploaded = True
                    st.session_state.pdf_filename = uploaded.name
                    st.session_state.pdf_text = corpus.full_text
                    st.session_state.pdf_token_count = len(corpus.full_text)
                    st.session_state.use_book_context = True
                    st.session_state.pdf_hash = corpus.pdf_hash
                    log_activity(
                        f"PDF processed (full): {uploaded.name} -- "
                        f"{len(corpus.figures)} figures, {len(corpus.text_chunks)} chunks",
                        icon="📄", level="success",
                    )
                else:
                    # Quick text-only path (legacy / fallback for huge PDFs)
                    extractor = _get_pdf_extractor(
                        config.gemini_api_key, config.db_path, db=None,
                        user_id=st.session_state.user_id,
                    )
                    text = extractor.extract_text(tmp_path)
                    st.session_state.pdf_uploaded = True
                    st.session_state.pdf_filename = uploaded.name
                    st.session_state.pdf_text = text
                    st.session_state.pdf_token_count = len(text)
                    st.session_state.use_book_context = len(text) <= MAX_BOOK_TOKENS
                    log_activity(
                        f"PDF processed (text only): {uploaded.name} ({len(text):,} chars)",
                        icon="📄", level="success",
                    )

                status.update(label="✅ PDF processed", state="complete")
                st.rerun()
            except Exception as e:
                status.update(label=f"❌ Error: {e}", state="error")
                st.error(f"Error: {e}")
                log_error(str(e))

    if st.session_state.pdf_uploaded:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.success(f"✅ {st.session_state.pdf_filename} processed.")
        with col2:
            if st.session_state.get("course_corpus") is not None:
                n_figs = len(st.session_state.course_corpus.figures)
                n_chunks = len(st.session_state.course_corpus.text_chunks)
                st.metric("Figures", n_figs)
            else:
                st.session_state.use_book_context = st.checkbox(
                    "Use entire book", value=st.session_state.use_book_context
                )

    ready = st.session_state.syllabus_text and st.session_state.subject_name
    nav_buttons(back=False, next_label="Generate Roadmap →", next_disabled=not ready, next_step=1)
