"""Step 2: Generate Roadmap — hierarchical topic tree from syllabus."""

import streamlit as st

from Smart_Teaching_Assistance.ui.steps._shared import (
    get_config, get_db, get_content_generator, save_course_state,
    nav_buttons, log_activity, log_error, logger,
    rotate_api_key,
)


def step_roadmap():
    if not st.session_state.syllabus_text:
        st.warning("⚠️ Provide a subject and syllabus first.")
        nav_buttons(next_=False, back_step=0)
        return

    st.markdown(
        f"Generate a **hierarchical roadmap (tree structure)** for "
        f"**{st.session_state.subject_name}**."
    )
    difficulty = st.selectbox(
        "Target Audience / Difficulty:",
        ["Undergraduate", "Graduate", "Professional", "High School"],
        index=0,
    )
    st.session_state.difficulty = difficulty

    btn_label = (
        "🔄 Regenerate Roadmap"
        if st.session_state.roadmap_generated
        else "🚀 Generate Roadmap"
    )

    if st.button(
        btn_label,
        type="primary" if not st.session_state.roadmap_generated else "secondary",
    ):
        # ── Live progress block ──────────────────────────────────
        # Streamlit's st.status is collapsed by default and the user
        # can't see whether anything is happening. We render an
        # EXPANDED live progress card with an elapsed-time counter
        # that ticks every second via a st.empty() rerun loop.
        import time as _time
        from datetime import datetime as _dt

        progress_box = st.empty()
        log_box = st.empty()

        def _render_progress(phase: str, started_at: float, msg: str = ""):
            elapsed = int(_time.time() - started_at)
            mm, ss = divmod(elapsed, 60)
            elapsed_str = f"{mm:02d}:{ss:02d}"
            # Indeterminate progress: cycle 5% → 95% over ~2 min,
            # capped at the phase label.
            frac = min(0.95, 0.05 + 0.90 * (elapsed / 120.0))
            with progress_box.container():
                st.markdown(
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
                          Generating roadmap
                        </div>
                        <div style="margin-left: auto; color: #94a3b8; font-family: monospace; font-size: 0.95rem;">
                          ⏱ {elapsed_str}
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

        # Phase 1: building source text + RAG retrieval
        started = _time.time()
        try:
            _render_progress("🧩 Preparing source", started,
                             "resolving RAG excerpts from the uploaded PDF")
            config = get_config()
            db = get_db(config)
            ContentGenerator = get_content_generator()
            gen = ContentGenerator(
                config.gemini_api_key,
                db,
                model_name=config.gemini_model,
                rotation_callback=rotate_api_key,
                user_id=st.session_state.user_id
            )

            # ── Build the source text ────────────────────────────
            # Priority order:
            #   1. RAG-retrieved excerpts from the uploaded PDF (if available)
            #   2. Raw pdf_text (legacy / quick mode)
            #   3. Just the syllabus
            source_parts: list[str] = [st.session_state.syllabus_text]

            if st.session_state.get("course_corpus") is not None:
                # Use RAG to pull the most relevant excerpts for this
                # subject. Creativity drops -- the user wants book-
                # faithful output, not LLM imagination.
                from Smart_Teaching_Assistance.ui.rag_bridge import get_rag_context
                rag_ctx = get_rag_context(
                    st.session_state.subject_name,
                    k_text=8, k_figures=2, rerank=True,
                )
                if rag_ctx.text_excerpts or rag_ctx.matching_figures:
                    rag_block = rag_ctx.as_prompt_block()
                    source_parts.append(
                        "\n\n--- RELEVANT BOOK EXCERPTS (RAG-retrieved) ---\n"
                        + rag_block
                        + "\n\nINSTRUCTION: Stay faithful to the book excerpts above. "
                        "Do NOT introduce topics that are not covered in the book. "
                        "If the book doesn't cover a sub-topic, omit it."
                    )

            source = "\n\n".join(source_parts)

            # Phase 2: calling the AI
            _render_progress("🤖 Calling AI", started,
                             f"model={config.gemini_model or 'gemini-flash'}, "
                             f"source={len(source):,} chars")
            log_activity("Generating roadmap...", icon="🗺️", level="progress")
            result = gen.generate_and_store_roadmap(
                source, difficulty, subject=st.session_state.subject_name
            )

            # Phase 3: parsing + storing
            _render_progress("📋 Parsing & storing", started,
                             f"{len(result.get('roadmap_text',''))} chars returned")

            # Store results
            st.session_state.roadmap_generated = True
            st.session_state.roadmap_text = result["roadmap_text"]
            st.session_state.roadmap_tree = result["roadmap_tree"]
            st.session_state.roadmap_structure = result["roadmap_structure"]
            st.session_state.roadmap_id = result.get("roadmap_id")

            # Clear downstream state
            st.session_state.lesson_plan_generated = False
            st.session_state.lesson_plan = {}
            st.session_state.detailed_notes_generated = False
            st.session_state.all_notes_data = []
            st.session_state.chapter_summaries = {}
            st.session_state.flashcards = []
            st.session_state.question_bank = []
            st.session_state.practice_exam = None
            st.session_state.glossary = []
            st.session_state.review_completed = False

            save_course_state()

            elapsed_total = int(_time.time() - started)
            mm, ss = divmod(elapsed_total, 60)
            progress_box.success(
                f"✅ Roadmap generated in {mm:02d}:{ss:02d} — "
                f"{len(result.get('roadmap_text',''))} chars, "
                f"ready for review below"
            )
            log_activity(
                f"Roadmap generated in {mm:02d}:{ss:02d}",
                icon="✅", level="success",
            )
            st.rerun()
        except Exception as e:
            progress_box.error(f"❌ Roadmap generation failed: {e}")
            err = str(e)
            if "timeout" in err.lower() or "deadline" in err.lower():
                st.error(
                    "Roadmap generation timed out. Please retry in a moment. "
                    "If this keeps happening, reduce input size or switch API key."
                )
            else:
                st.error(f"Error: {e}")
            log_error(f"Roadmap error: {e}")

    # ── View / Edit ──────────────────────────────────────────────────────
    if st.session_state.roadmap_generated:
        st.success("✅ Roadmap Ready")
        with st.expander("👁️ View & Edit Roadmap Text"):
            st.markdown(
                "You can manually adjust the T-numbered roadmap below. "
                "Click 'Apply Changes' to re-parse the tree."
            )
            edited_roadmap = st.text_area(
                "Roadmap Content", value=st.session_state.roadmap_text, height=400
            )
            btn_col, _ = st.columns([1, 4])
            with btn_col:
                if st.button("💾 Apply Manual Changes", type="secondary", use_container_width=True):
                    try:
                        config = get_config()
                        ContentGenerator = get_content_generator()
                        gen = ContentGenerator(config.gemini_api_key, user_id=st.session_state.user_id)
                        st.session_state.roadmap_text = edited_roadmap
                        st.session_state.roadmap_tree = gen.parse_roadmap(edited_roadmap)
                        save_course_state()
                        st.toast("Roadmap updated and re-parsed!")
                        log_activity("Roadmap manually edited", icon="✏️", level="info")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to parse edited roadmap: {e}")
                        log_error(f"Roadmap parse error: {e}")

    nav_buttons(
        next_label="Lesson Plan →",
        next_disabled=not st.session_state.roadmap_generated,
        back_step=0,
        next_step=2,
    )
