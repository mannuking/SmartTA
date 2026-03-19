"""Step 6: Export — download all generated course materials."""

import streamlit as st
import json

from Smart_Teaching_Assistance.ui.steps._shared import (
    get_config, get_db, get_content_generator, get_export_manager,
    nav_buttons, log_activity, log_error, logger,
)


def step_export():
    if not st.session_state.detailed_notes_generated:
        st.warning("⚠️ Generate notes first.")
        nav_buttons(next_=False, back_step=4)
        return

    st.markdown("Download your full course package with all generated materials.")
    ContentGenerator = get_content_generator()
    ExportManager = get_export_manager()

    # ── Course stats ─────────────────────────────────────────────────────
    stats_cols = st.columns(4)
    with stats_cols[0]:
        total_words = sum(
            len(el.get("content", "").split())
            for n in st.session_state.all_notes_data
            for el in n.get("elements", [])
            if el.get("type") in ("text", "heading", "callout")
        )
        st.metric("📖 Total Words", f"{total_words:,}")
    with stats_cols[1]:
        st.metric("📚 Topics", len(st.session_state.all_notes_data))
    with stats_cols[2]:
        reading_time = max(1, total_words // 200)
        st.metric("⏱ Est. Reading Time", f"{reading_time} min")
    with stats_cols[3]:
        st.metric("🃏 Flashcards", len(st.session_state.flashcards))

    st.markdown("---")

    # ── Core Package ─────────────────────────────────────────────────────
    st.subheader("📄 Core Package")
    exp = ExportManager(db_path=get_config().db_path)

    try:
        docx_p = exp.export_full_course_docx(
            st.session_state.subject_name,
            st.session_state.all_notes_data,
            st.session_state.answer_key_text,
            chapter_summaries=st.session_state.chapter_summaries,
            glossary=st.session_state.glossary,
        )
        with open(docx_p, "rb") as f:
            docx_data = f.read()
        st.download_button(
            "💎 Full Course (DOCX)",
            data=docx_data,
            file_name=f"{st.session_state.subject_name}.docx",
            type="primary",
            use_container_width=True,
        )
    except Exception as e:
        st.error(f"DOCX Export Error: {e}")
        logger.error(f"DOCX export failed: {e}", exc_info=True)
        log_error(f"DOCX export: {e}")

    try:
        pdf_p = exp.export_full_course_pdf(
            st.session_state.subject_name,
            st.session_state.all_notes_data,
            st.session_state.answer_key_text,
            chapter_summaries=st.session_state.get("chapter_summaries"),
            glossary=st.session_state.get("glossary"),
        )
        with open(pdf_p, "rb") as f:
            pdf_data = f.read()
        st.download_button(
            "📕 Full Course (PDF)",
            data=pdf_data,
            file_name=f"{st.session_state.subject_name}.pdf",
            use_container_width=True,
        )
    except Exception as e:
        st.error(f"PDF Export Error: {e}")
        log_error(f"PDF export: {e}")

    if st.session_state.notes_markdown:
        st.download_button(
            "📝 Full Course (Markdown)",
            data=st.session_state.notes_markdown,
            file_name=f"{st.session_state.subject_name}.md",
            use_container_width=True,
        )

    log_activity("Export page loaded", icon="📤", level="info")
    nav_buttons(next_=False, back_step=4)
