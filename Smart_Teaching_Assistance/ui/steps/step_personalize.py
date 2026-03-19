"""Step 5: Personalize — adapt notes for specific audience profiles."""

import streamlit as st
import time as _time

from Smart_Teaching_Assistance.ui.steps._shared import (
    get_config, get_db, get_content_generator, save_course_state,
    nav_buttons, render_note_elements,
    log_activity, log_error, logger,
    topic_heading_level, clean_topic_title, topic_display_label,
    rotate_api_key,
)


PRESETS = {
    "Visual Learner": (
        "Students who learn best through diagrams, charts, and visual representations. "
        "Use more visual analogies and spatial reasoning examples."
    ),
    "Exam-Focused (Last-Minute)": (
        "Students preparing for exams with limited time. Prioritize key formulas, "
        "common exam questions, mnemonics, and quick-reference summaries. "
        "Minimize background context."
    ),
    "Beginner (No Background)": (
        "Complete beginners with no prior knowledge of the subject. Use everyday "
        "analogies, avoid jargon, explain every term from scratch, and build up gradually."
    ),
    "Professional / Applied": (
        "Working professionals who need practical, applied knowledge. Focus on "
        "real-world applications, industry examples, and skip purely theoretical derivations."
    ),
    "Graduate / Advanced": (
        "Advanced students who already know the basics. Skip introductory material, "
        "focus on edge cases, proofs, advanced applications, and research connections."
    ),
    "Non-Native English Speaker": (
        "Students for whom English is a second language. Use simpler sentence structures, "
        "define all technical terms, avoid idioms, and provide clear step-by-step explanations."
    ),
}


def step_personalize():
    if not st.session_state.detailed_notes_generated:
        st.warning("⚠️ Generate notes first.")
        nav_buttons(next_=False, back_step=3)
        return

    st.markdown("Adapt your notes for a specific **audience profile** or choose a preset.")

    # ── Presets ───────────────────────────────────────────────────────────
    st.subheader("🎯 Quick Presets")
    preset_cols = st.columns(3)

    for idx, (name, desc) in enumerate(PRESETS.items()):
        col = preset_cols[idx % 3]
        with col:
            if st.button(f"📌 {name}", key=f"preset_{idx}", use_container_width=True):
                st.session_state.audience_input_val = desc
                st.rerun()

    audience = st.text_area(
        "🎯 Target Audience Profile",
        placeholder="e.g., Beginners with no background in the subject...",
        value=st.session_state.get("audience_input_val", ""),
        height=120,
        key="audience_input",
    )
    st.session_state.audience_input_val = audience

    # ── Personalize ──────────────────────────────────────────────────────
    if st.button("🔄 Personalize Notes", type="primary", disabled=not audience):
        with st.status("🔄 Personalizing content...") as pers_s:
            try:
                t0 = _time.time()
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

                personalized_notes = []
                total = len(st.session_state.all_notes_data)
                prog = st.progress(0)
                log_activity(f"Personalizing {total} topics...", icon="👥", level="progress")

                for idx, note in enumerate(st.session_state.all_notes_data):
                    prog.progress((idx + 1) / total)
                    st.write(f"🔄 Personalizing: {note['id']} — {note['title']}")

                    new_elements = []
                    for el in note.get("elements", []):
                        el_type = el.get("type", "")
                        callout_type = el.get("callout_type", "")

                        if el_type == "callout" and any(
                            k in callout_type for k in ["Analogy", "Practice", "Understanding"]
                        ):
                            prompt = (
                                f"Rewrite this {callout_type} for the following audience:\n"
                                f"Audience: {audience}\n"
                                f"Subject: {st.session_state.subject_name}\n"
                                f"Topic: {note['title']}\n\n"
                                f"Original:\n{el.get('content', '')}\n\n"
                                f"Keep the same educational depth but adapt language, examples, "
                                f"and references for this specific audience. Output only the new content text."
                            )
                            try:
                                new_content = gen._generate_text(
                                    prompt, 0.5, f"personalize_{note['id']}_{el_type}"
                                )
                                new_el = dict(el)
                                new_el["content"] = new_content
                                new_elements.append(new_el)
                            except Exception:
                                new_elements.append(el)
                        else:
                            new_elements.append(el)

                    personalized_notes.append({
                        "id": note["id"],
                        "title": note["title"],
                        "elements": new_elements,
                    })

                st.session_state.personalized_versions = personalized_notes
                save_course_state()
                pers_s.update(label=f"✅ Personalized {total} topics!", state="complete")
                log_activity(f"Personalized {total} topics", icon="✅", level="success")
                st.rerun()
            except Exception as e:
                st.error(f"Personalization error: {e}")
                log_error(f"Personalization error: {e}")

    # ── Preview ──────────────────────────────────────────────────────────
    if st.session_state.personalized_versions:
        st.success(f"✅ {len(st.session_state.personalized_versions)} personalized topics ready")
        with st.expander("👁️ Preview Personalized Notes"):
            for note in st.session_state.personalized_versions[:3]:
                hlevel = topic_heading_level(note["id"])
                st.markdown(f"{'#' * hlevel} {topic_display_label(note)}")
                render_note_elements(note, heading_offset=note["id"].count('.'))
                st.markdown("---")

    nav_buttons(next_label="Export →", back_step=3, next_step=5)
