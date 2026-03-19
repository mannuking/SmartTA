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
        with st.status("🔄 Generating Roadmap...") as s:
            try:
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

                source = st.session_state.syllabus_text
                if st.session_state.use_book_context and st.session_state.pdf_text:
                    source += "\n\n--- BOOK CONTEXT ---\n" + st.session_state.pdf_text

                log_activity("Generating roadmap...", icon="🗺️", level="progress")
                result = gen.generate_and_store_roadmap(
                    source, difficulty, subject=st.session_state.subject_name
                )

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
                s.update(label="✅ Roadmap Generated!", state="complete")
                log_activity("Roadmap generated successfully", icon="✅", level="success")
                st.rerun()
            except Exception as e:
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
