"""Step 6: Export — download all generated course materials."""

import streamlit as st
import json
import time as _time

from Smart_Teaching_Assistance.ui.steps._shared import (
    get_config, get_db, get_content_generator, get_export_manager,
    nav_buttons, log_activity, log_error, logger,
)


def _ensure_notes_for_export():
    """Make sure the session has something exportable.

    The user may have arrived at Step 6 via "Skip to Export" from
    Step 4, or via direct navigation. In those cases `detailed_notes`
    might be missing even though the lesson plan IS in session. We
    auto-generate the notes on-the-fly using the same path as Step 4
    (but synchronously, not the background-worker variant, so the
    export page can render right after).

    If we don't even have a lesson plan yet, we build a minimal
    exportable structure from the roadmap text -- it's not as rich
    as proper notes, but it gives the user SOMETHING to download
    instead of a hard "Generate notes first" wall.
    """
    if st.session_state.get("detailed_notes_generated"):
        return  # already good

    has_lesson_plan = bool(st.session_state.get("lesson_plan"))

    # Live progress card so the user sees what's happening
    progress = st.empty()
    started = _time.time()

    def _render(phase: str, msg: str = "", frac: float = 0.1):
        elapsed = int(_time.time() - started)
        mm, ss = divmod(elapsed, 60)
        progress.markdown(
            f"""
            <div style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
                        border: 1px solid #334155; border-radius: 12px;
                        padding: 1.25rem 1.5rem; margin: 0.5rem 0 1rem 0;
                        box-shadow: 0 4px 12px rgba(0,0,0,0.25);">
              <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem;">
                <div style="width: 14px; height: 14px; border-radius: 50%;
                            background: #38bdf8; box-shadow: 0 0 0 0 rgba(56,189,248,0.7);
                            animation: smartta-pulse 1.5s infinite;"></div>
                <div style="color: #f1f5f9; font-weight: 600; font-size: 1.05rem;">
                  Preparing export
                </div>
                <div style="margin-left: auto; color: #94a3b8; font-family: monospace; font-size: 0.95rem;">
                  ⏱ {mm:02d}:{ss:02d}
                </div>
              </div>
              <div style="color: #cbd5e1; font-size: 0.9rem; margin-bottom: 0.75rem;">
                {phase}{(' — ' + msg) if msg else ''}
              </div>
              <div style="background: #1e293b; border-radius: 6px; overflow: hidden; height: 6px;">
                <div style="background: linear-gradient(90deg, #38bdf8, #818cf8);
                            height: 100%; width: {frac*100:.0f}%;
                            transition: width 0.5s ease;"></div>
              </div>
            </div>
            <style>
              @keyframes smartta-pulse {{
                0%   {{ box-shadow: 0 0 0 0 rgba(56,189,248,0.7); }}
                70%  {{ box-shadow: 0 0 0 12px rgba(56,189,248,0); }}
                100% {{ box-shadow: 0 0 0 0 rgba(56,189,248,0); }}
              }}
            </style>
            """,
            unsafe_allow_html=True,
        )

    try:
        if has_lesson_plan:
            # We have a lesson plan -> generate notes synchronously
            _render("📝 Generating notes from your lesson plan", "this takes a few seconds", 0.15)
            config = get_config()
            db = get_db(config)
            ContentGenerator = get_content_generator()
            gen = ContentGenerator(
                config.gemini_api_key,
                db,
                model_name=config.gemini_model,
                user_id=st.session_state.user_id,
            )

            from Smart_Teaching_Assistance.ui.rag_bridge import get_rag_context
            rag_ctx = get_rag_context(
                st.session_state.subject_name,
                k_text=8, k_figures=2, rerank=True,
            )
            rag_block = rag_ctx.as_prompt_block() if (
                rag_ctx.text_excerpts or rag_ctx.matching_figures
            ) else ""

            _render("🤖 Calling AI for each topic", "stay on this page", 0.4)
            # The real entry point is generate_detailed_notes_recursive.
            # It returns (notes_data, answer_key_text).
            highlight_list = []
            highlights = st.session_state.get("highlighted_topics_input", "")
            if highlights:
                highlight_list = [h.strip() for h in highlights.split(",") if h.strip()]

            def _book_context_for(topic_id: str, topic_title: str) -> str:
                # Re-pull RAG context for each topic so the per-topic
                # prompt has the most relevant book excerpts.
                try:
                    sub_ctx = get_rag_context(
                        topic_title or topic_id,
                        section_id=topic_id,
                        k_text=5, k_figures=2, rerank=True,
                    )
                    return sub_ctx.as_prompt_block()
                except Exception:
                    return ""

            notes_data, answer_key = gen.generate_detailed_notes_recursive(
                lesson_plan=st.session_state.lesson_plan,
                subject=st.session_state.subject_name,
                difficulty=st.session_state.get("difficulty", "Undergraduate"),
                highlights=highlight_list,
                temperature=0.7,
                existing_notes=st.session_state.get("all_notes_data") or None,
                roadmap_tree=st.session_state.get("roadmap_tree") or None,
                book_context_fn=_book_context_for,
            )
            st.session_state.all_notes_data = notes_data
            if answer_key:
                st.session_state.answer_key_text = answer_key
            st.session_state.detailed_notes_generated = True

            # If the user uploaded a PDF, auto-place the best-matching
            # extracted figure at each sub-section. Without this, the
            # exported PDF has zero images -- the figure_block elements
            # never get added on the Skip-to-Export path because
            # place_figures_into_notes was only called from Step 4's
            # background worker. Now we call it here too.
            if st.session_state.get("course_corpus") is not None:
                try:
                    from Smart_Teaching_Assistance.ui.rag_bridge import place_figures_into_notes
                    notes_data = place_figures_into_notes(notes_data)
                    st.session_state.all_notes_data = notes_data
                    placed = sum(
                        1 for n in notes_data for e in n.get("elements", [])
                        if e.get("type") == "figure_block"
                    )
                    if placed:
                        log_activity(
                            f"Placed {placed} figure(s) from the uploaded PDF into the notes.",
                            icon="🖼️", level="success",
                        )
                except Exception as fig_err:
                    logger.debug(f"figure placement skipped: {fig_err}")

            _render("✅ Notes ready", f"loaded {len(notes_data)} topic blocks", 1.0)
            progress.empty()
            log_activity(
                f"Auto-generated notes on Skip-to-Export: {len(notes_data)} topics",
                icon="📝", level="success",
            )
            return

        # No lesson plan either. Build a minimal exportable structure
        # from the roadmap text so the user gets a real file.
        if st.session_state.get("roadmap_text"):
            _render("📋 Building a quick export from your roadmap", "no lesson plan yet", 0.3)
            # Convert roadmap_text into a single-topic notes structure
            # so ExportManager can produce a real PDF/DOCX.
            from Smart_Teaching_Assistance.ui.notes_from_roadmap import (
                build_notes_from_roadmap,
            )
            notes_data = build_notes_from_roadmap(
                st.session_state.roadmap_text,
                st.session_state.subject_name,
            )
            st.session_state.all_notes_data = notes_data
            st.session_state.detailed_notes_generated = True
            _render("✅ Quick export ready", f"from roadmap ({len(notes_data)} topic(s))", 1.0)
            progress.empty()
            log_activity(
                f"Built quick export from roadmap (no lesson plan): "
                f"{len(notes_data)} topic(s)",
                icon="📋", level="info",
            )
            return

        # Nothing at all to export
        progress.empty()
        st.error(
            "❌ Nothing to export yet. Go back to **Step 1** to set up a "
            "subject and syllabus, then return here."
        )
        nav_buttons(next_=False, back_step=4)
        return False
    except Exception as e:
        progress.empty()
        st.error(
            f"❌ Auto-generation failed: {e}\n\n"
            f"You can go back to **Step 4 (Detailed Notes)** and click "
            f"\"Generate Notes\" manually."
        )
        log_error(f"Auto-generate-on-skip failed: {e}")
        logger.exception("auto-generate notes failed")
        return False
    return True


def step_export():
    if not st.session_state.detailed_notes_generated:
        # Try to auto-generate. If even that fails, the function above
        # already showed an error and we should bail.
        ok = _ensure_notes_for_export()
        if not ok or not st.session_state.detailed_notes_generated:
            return
        # Otherwise: we have notes now, fall through to the export UI.

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

    # ── Figure re-attachment ──────────────────────────────────────────────
    # The notes in session_state might not have figure_block elements
    # if they were generated before the figure_placer fix. The user
    # can click this button to force a re-run of place_figures_into_notes
    # against the current course_corpus (if any).
    fig_cols = st.columns([2, 3])
    with fig_cols[0]:
        if st.button(
            "🖼️ Re-attach PDF figures to notes",
            use_container_width=True,
            help="Re-run figure_placer to inject the 10 figures from your PDF into the matching topics.",
        ):
            try:
                from Smart_Teaching_Assistance.ui.rag_bridge import place_figures_into_notes
                before = sum(
                    1 for n in st.session_state.all_notes_data
                    for e in n.get("elements", [])
                    if e.get("type") == "figure_block"
                )
                st.session_state.all_notes_data = place_figures_into_notes(
                    st.session_state.all_notes_data
                )
                after = sum(
                    1 for n in st.session_state.all_notes_data
                    for e in n.get("elements", [])
                    if e.get("type") == "figure_block"
                )
                placed = after - before
                log_activity(
                    f"Re-attached figures: +{placed} new (now {after} total figure_block elements)",
                    icon="🖼️", level="success",
                )
                st.toast(f"Placed {placed} figure(s) — now {after} figure_block(s) in notes.")
                st.rerun()
            except Exception as e:
                st.error(f"Re-attach failed: {e}")
                logger.error(f"Re-attach figures failed: {e}", exc_info=True)
    with fig_cols[1]:
        # Show how many figure_block elements are currently in notes
        total_fb = sum(
            1 for n in st.session_state.all_notes_data
            for e in n.get("elements", [])
            if e.get("type") == "figure_block"
        )
        # And how many source figures are available
        corpus = st.session_state.get("course_corpus")
        n_source = len(corpus.figures) if corpus and hasattr(corpus, 'figures') else 0
        st.markdown(
            f"<small>📦 Source figures: **{n_source}**  •  "
            f"🖼️ Placed in notes: **{total_fb}**</small>",
            unsafe_allow_html=True,
        )
        if n_source and not total_fb:
            st.warning(
                f"⚠️ {n_source} figure(s) available from your PDF but none placed in the notes yet. "
                f"Click 'Re-attach' to add them."
            )

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
