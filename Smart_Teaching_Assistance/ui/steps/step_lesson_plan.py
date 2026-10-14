"""Step 3: Lesson Plan — generate structured teaching strategies for every topic.

Uses BackgroundLPWorker so the user can navigate away without killing generation.
"""

import streamlit as st
import time as _time
from datetime import timedelta

from Smart_Teaching_Assistance.ui.steps._shared import (
    get_config, get_db, get_content_generator, save_course_state,
    nav_buttons, count_tree, fmt_duration,
    log_activity, log_error, logger, PauseRequested,
    rotate_api_key,
)
from Smart_Teaching_Assistance.ui.background_worker import BackgroundLPWorker, register_worker, unregister_worker


_LP_SECTION_FIELDS = [
    ("learning_objectives", "Learning Objectives"),
    ("key_concepts", "Key Concepts & Definitions"),
    ("instructional_content", "Instructional Content Outline"),
    ("engagement_activities", "Engagement Strategies & Activities"),
    ("assessment_methods", "Assessment Methods & Checkpoints"),
    ("potential_challenges", "Potential Challenges & Misconceptions"),
    ("eli5_suggestion", "ELI5 for Complex Concepts"),
    ("additional_notes", "Educator Notes"),
]


def _bullets_to_text(value):
    if isinstance(value, list):
        return "\n".join(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, str):
        return value.strip()
    return ""


def _text_to_bullets(value: str):
    items = []
    for line in (value or "").splitlines():
        clean = line.strip().lstrip("-• ").strip()
        if clean:
            items.append(clean)
    return items


def _build_topic_markdown(topic: dict) -> str:
    """Rebuild the markdown display content from structured editor fields."""
    structured = topic.get("structured_content", {}) or {}
    blocks = []

    for key, title in _LP_SECTION_FIELDS:
        val = (structured.get(key, "") or "").strip()
        if val:
            blocks.append(f"### {title}\n{val}")

    mega = (topic.get("teaching_prompt", "") or "").strip()
    if mega:
        blocks.append(f"### Section Mega Prompt\n{mega}")

    coverage = topic.get("coverage_contract", {}) or {}
    prereq = _bullets_to_text(coverage.get("prerequisites", []))
    must_include = _bullets_to_text(coverage.get("must_include", []))
    must_exclude = _bullets_to_text(coverage.get("must_exclude", []))
    bridge_in = str(coverage.get("bridge_from_previous", "") or "").strip()
    handoff = str(coverage.get("handoff_to_next", "") or "").strip()

    if prereq:
        blocks.append(f"### Prerequisites\n{prereq}")
    if must_include:
        blocks.append(f"### Must Include\n{must_include}")
    if must_exclude:
        blocks.append(f"### Must Exclude (Avoid Redundancy)\n{must_exclude}")
    if bridge_in:
        blocks.append(f"### Continuity Bridge From Previous\n{bridge_in}")
    if handoff:
        blocks.append(f"### Continuity Handoff To Next\n{handoff}")

    return "\n\n".join(blocks).strip()


# ── Helper: get or init the background LP worker ────────────────────────────

def _get_lp_worker() -> BackgroundLPWorker:
    if "lp_worker" not in st.session_state or st.session_state.lp_worker is None:
        st.session_state.lp_worker = BackgroundLPWorker()
    return st.session_state.lp_worker


# ── Main step function ───────────────────────────────────────────────────────

def step_lesson_plan():
    if not st.session_state.roadmap_generated:
        st.warning("⚠️ Generate a roadmap first.")
        nav_buttons(next_=False, back_step=1)
        return

    save_course_state()
    st.markdown("Generate a structured **lesson plan** for every topic.")

    top_topics = st.session_state.roadmap_tree.get("topics", [])
    total_nodes = count_tree(top_topics)
    completed_nodes = count_tree(st.session_state.lesson_plan.get("topics", []))
    worker = _get_lp_worker()

    # Sync partial results from worker on every page render
    _sync_lp_worker_to_session(worker, total_nodes)
    worker = _get_lp_worker()  # re-read after sync (may have been reset)
    completed_nodes = count_tree(st.session_state.lesson_plan.get("topics", []))

    # ── Status banners ───────────────────────────────────────────────
    gen_status = st.session_state.get("generation_status", "idle")
    is_interrupted = (
        gen_status in ("generating_lp", "paused_lp")
        and not worker.is_running
        and worker.status == "idle"
    )

    if is_interrupted:
        if completed_nodes > 0:
            st.warning(
                f"⚠️ Lesson plan generation was **interrupted** — "
                f"**{completed_nodes} / {total_nodes}** topics saved. "
                "Click **Generate/Resume** to continue."
            )
        else:
            st.warning(
                "⚠️ Lesson plan generation was **interrupted** before any topics completed. "
                "Click **Generate/Resume** to restart."
            )
    elif 0 < completed_nodes < total_nodes and not worker.is_running:
        st.warning(f"⏩ Partial progress: **{completed_nodes} / {total_nodes}** generated.")
    elif completed_nodes >= total_nodes and not worker.is_running:
        st.success(f"✅ Lesson plan complete! ({completed_nodes} topics)")

    llm_temp = st.slider("🌡️ AI Creativity", 0.1, 1.0, 0.4, step=0.05, key="temp_lp")

    # ── Auto-resume from dashboard ───────────────────────────────────
    auto_resume = st.session_state.pop("_auto_resume", None)

    # ── Running / Pausing: show live progress ────────────────────────
    if worker.is_running or worker.status == "pausing":
        _render_lp_live_progress(worker, total_nodes)
    elif worker.status == "paused":
        st.info(
            f"⏸️ Generation paused — **{completed_nodes} / {total_nodes}** "
            "topics saved. Click **Generate/Resume** to continue."
        )
        if auto_resume == "lp" or st.button("🚀 Generate/Resume Lesson Plan", type="primary"):
            _start_lp_generation(total_nodes, llm_temp, is_regenerating=False)
    elif worker.status == "error":
        prog = worker.progress
        st.error(f"❌ Generation error: {prog.get('error', 'Unknown error')}")
        if auto_resume == "lp" or st.button("🚀 Retry Lesson Plan", type="primary"):
            _start_lp_generation(total_nodes, llm_temp, is_regenerating=False)
    else:
        # idle — show generate / regenerate button
        is_regen = st.session_state.lesson_plan_generated
        lp_btn_label = "🔄 Regenerate Lesson Plan" if is_regen else "🚀 Generate/Resume Lesson Plan"
        if auto_resume == "lp" or st.button(
            lp_btn_label,
            type="primary" if not is_regen else "secondary",
        ):
            _start_lp_generation(total_nodes, llm_temp, is_regenerating=is_regen)

    # ── Navigator ────────────────────────────────────────────────────
    _render_lp_navigator()

    nav_buttons(
        next_label="Detailed Notes →",
        next_disabled=not st.session_state.lesson_plan_generated,
        back_step=1,
        next_step=3,
    )


# ── Private: Start background generation ─────────────────────────────────────

def _start_lp_generation(total_nodes: int, llm_temp: float, is_regenerating: bool):
    worker = _get_lp_worker()
    if worker.is_running:
        logger.warning("Ignoring duplicate LP generation request — worker already running")
        return

    if is_regenerating:
        st.session_state.lesson_plan = {}
        st.session_state.lesson_plan_generated = False
        # Clear downstream
        st.session_state.detailed_notes_generated = False
        st.session_state.all_notes_data = []
        st.session_state.chapter_summaries = {}
        st.session_state.flashcards = []
        st.session_state.question_bank = []
        st.session_state.practice_exam = None
        st.session_state.glossary = []
        st.session_state.review_completed = False
        save_course_state()

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

    # If previously completed or errored, reset worker
    if worker.status in ("completed", "error"):
        st.session_state.lp_worker = BackgroundLPWorker()
        worker = st.session_state.lp_worker

    gen_kwargs = dict(
        subject=st.session_state.subject_name,
        roadmap_tree=st.session_state.roadmap_tree,
        difficulty_level=st.session_state.difficulty,
        temperature=llm_temp,
        existing_plan=None if is_regenerating else st.session_state.lesson_plan,
        partial_update_cb=worker.update_partial,
    )

    st.session_state.pop("_lp_terminal_rerun", None)
    st.session_state.pop("_global_terminal_rerun", None)

    started = worker.start(
        gen_callable=gen.generate_lesson_plan,
        gen_kwargs=gen_kwargs,
        existing_plan=st.session_state.lesson_plan if not is_regenerating else None,
    )

    if started:
        from Smart_Teaching_Assistance.ui.state_manager import set_generation_status, update_sidebar_progress
        set_generation_status("generating_lp")
        cid = st.session_state.get("course_id")
        if cid is not None:
            register_worker(cid, "lp", worker)
        log_activity("Lesson plan generation started (background)", icon="🚀", level="progress")
        update_sidebar_progress(task="Lesson Plan")
    st.rerun()


# ── Private: Sync worker state → session state ──────────────────────────────

def _sync_lp_worker_to_session(worker: BackgroundLPWorker, total_nodes: int):
    from Smart_Teaching_Assistance.ui.state_manager import (
        update_sidebar_progress, clear_sidebar_progress, set_generation_status,
    )

    if worker.status == "idle":
        return

    prog = worker.progress

    # Always sync partial plan
    partial = worker.partial_plan
    if partial and partial.get("topics"):
        partial_count = count_tree(partial.get("topics", []))
        current_count = count_tree(st.session_state.lesson_plan.get("topics", []))
        if partial_count > current_count:
            st.session_state.lesson_plan = partial
            if partial_count % 3 == 0:
                save_course_state()

    if worker.status in ("running", "pausing"):
        update_sidebar_progress(
            task="Lesson Plan",
            frac=prog.get("frac", 0.0),
            label=prog.get("label", ""),
            eta=prog.get("eta_str", ""),
            elapsed=prog.get("elapsed_str", ""),
        )

    elif worker.status == "completed":
        if worker.results is None:
            return
        lp = worker.results
        st.session_state.lesson_plan = lp
        if count_tree(lp.get("topics", [])) >= total_nodes:
            st.session_state.lesson_plan_generated = True
        save_course_state()
        set_generation_status("idle")
        log_activity(
            f"Lesson plan completed: {count_tree(lp.get('topics', []))} topics",
            icon="✅", level="success",
        )
        clear_sidebar_progress()
        cid = st.session_state.get("course_id")
        if cid is not None:
            unregister_worker(cid, "lp")
        st.session_state.lp_worker = BackgroundLPWorker()

    elif worker.status == "paused":
        partial = worker.partial_plan
        if partial and partial.get("topics"):
            st.session_state.lesson_plan = partial
            save_course_state()
        set_generation_status("paused_lp")
        log_activity(
            f"Lesson plan paused at {count_tree(st.session_state.lesson_plan.get('topics', []))}/{total_nodes}",
            icon="⏸️", level="warning",
        )
        clear_sidebar_progress()

    elif worker.status == "error":
        err_msg = prog.get("error", "Unknown error")
        log_error(f"Lesson plan error: {err_msg}")
        partial = worker.partial_plan
        if partial and partial.get("topics"):
            st.session_state.lesson_plan = partial
            save_course_state()
        set_generation_status("idle")
        clear_sidebar_progress()
        cid = st.session_state.get("course_id")
        if cid is not None:
            unregister_worker(cid, "lp")


# ── Private: Live progress display (auto-refreshing fragment) ────────────────

# Rerun twice per second so elapsed/ETA tick visibly. Streamlit fragments
# re-rendering at 0.5s is fine for a single row of metrics (negligible CPU).
@st.fragment(run_every=timedelta(milliseconds=500))
def _render_lp_live_progress(worker: BackgroundLPWorker, total_nodes: int):
    prog = worker.progress
    status = prog.get("status", "idle")
    completed = prog.get("completed", 0)
    total = prog.get("total", total_nodes)
    frac = prog.get("frac", 0.0)
    message = prog.get("message", "")
    elapsed_str = prog.get("elapsed_str", "")
    eta_str = prog.get("eta_str", "")

    _sync_lp_worker_to_session(worker, total_nodes)

    st.markdown("### 📝 Generating Lesson Plan...")
    st.progress(frac)

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("⏱️ Elapsed", elapsed_str or "0s")
    mc2.metric("📊 Progress", f"{completed} / {total}")
    mc3.metric("⏳ ETA", eta_str or "Calculating...")
    mc4.metric("📝 Status", "Running" if status == "running" else status.title())

    if message:
        st.markdown(f"🔄 **{message}** · {frac*100:.0f}%")

    if status == "running":
        if st.button("⏸️ Pause Generation", type="secondary", key="_lp_pause"):
            worker.pause()
    elif status == "pausing":
        st.info("⏳ Pausing after current topic...")

    # Inline partial preview
    partial = st.session_state.get("lesson_plan", {})
    flat_topics = []
    def _flatten(nodes, depth=0):
        for n in nodes:
            flat_topics.append({"node": n, "depth": depth, "id": n.get("id", n.get("title", ""))})
            for sub_key in ("subtopics", "subsubtopics", "subsubsubtopics"):
                if sub_key in n and n[sub_key]:
                    _flatten(n[sub_key], depth + 1)
    _flatten(partial.get("topics", []))

    if flat_topics:
        col1, col2 = st.columns([1, 2.5])
        with col1:
            with st.container(height=350):
                for item in flat_topics:
                    n = item["node"]
                    indent = "  " * item["depth"]
                    label = f"{indent}{n.get('id', '')} {n.get('title', 'Untitled')}".strip()
                    is_active = item["id"] == st.session_state.get("active_lp_id", flat_topics[0]["id"])
                    if st.button(label, key=f"lplive_{item['id']}", type="primary" if is_active else "secondary", use_container_width=True):
                        st.session_state.active_lp_id = item["id"]
        with col2:
            active_id = st.session_state.get("active_lp_id", flat_topics[0]["id"])
            topic = next((t["node"] for t in flat_topics if t["id"] == active_id), flat_topics[-1]["node"])
            st.markdown(f"## {topic.get('id', '')} {topic.get('title', 'Untitled')}")
            with st.container(border=True):
                st.markdown(topic.get("content", "_Generating..._"))

    # If worker finished between refreshes, trigger ONE full-app rerun
    if status in ("completed", "paused", "error"):
        if not st.session_state.get("_lp_terminal_rerun"):
            st.session_state._lp_terminal_rerun = True
            st.rerun(scope="app")


# ── Private: Navigator ──────────────────────────────────────────────────────

def _render_lp_navigator():
    if not st.session_state.lesson_plan or not st.session_state.lesson_plan.get("topics"):
        return

    # Don't duplicate navigator if live progress is already showing it
    worker = _get_lp_worker()
    if worker.is_running or worker.status == "pausing":
        return

    st.markdown("### 👁️ Lesson Plan Navigator")

    flat_topics = []
    def flatten(nodes, depth=0):
        for n in nodes:
            flat_topics.append({
                "node": n, "depth": depth,
                "id": n.get("id", n.get("title", "")),
            })
            for sub_key in ("subtopics", "subsubtopics", "subsubsubtopics"):
                if sub_key in n and n[sub_key]:
                    flatten(n[sub_key], depth + 1)
    flatten(st.session_state.lesson_plan.get("topics", []))

    if not flat_topics:
        return

    col_tree, col_content = st.columns([1, 2])

    with col_tree:
        st.caption("Navigation Tree")
        with st.container(height=500, border=True):
            if "active_lp_id" not in st.session_state or not st.session_state.active_lp_id:
                st.session_state.active_lp_id = flat_topics[0]["id"]

            for item in flat_topics:
                n = item["node"]
                indent = "  " * item["depth"]
                label = f"{indent}{n.get('id', '')} {n.get('title', 'Untitled')}".strip()
                is_active = item["id"] == st.session_state.active_lp_id

                if st.button(
                    label,
                    key=f"lp_nav_{item['id']}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state.active_lp_id = item["id"]
                    st.rerun()

    with col_content:
        active_item = next(
            (t for t in flat_topics if t["id"] == st.session_state.active_lp_id),
            flat_topics[0],
        )
        topic = active_item["node"]
        st.markdown(f"## {topic.get('id', '')} {topic.get('title', 'Untitled')}")

        tab_preview, tab_edit = st.tabs(["Preview", "Guided Edit"])

        with tab_preview:
            with st.container(border=True):
                st.markdown(topic.get("content", "_No strategy details found._"))
                if not topic.get("content") and topic.get("structured_content"):
                    for val in topic["structured_content"].values():
                        if val:
                            st.markdown(val)

        with tab_edit:
            structured = topic.get("structured_content", {}) or {}
            coverage = topic.get("coverage_contract", {}) or {}

            st.caption("Edit with structured fields. Save updates refreshes preview and downstream notes context.")
            with st.form(key=f"lp_guided_edit_{topic.get('id', 'topic')}"):
                st.markdown("### Core Teaching Plan")
                edited_structured = {}
                for key, label in _LP_SECTION_FIELDS:
                    edited_structured[key] = st.text_area(
                        label,
                        value=structured.get(key, ""),
                        height=110,
                        key=f"lp_edit_{topic.get('id', 'topic')}_{key}",
                    )

                st.markdown("### Section Mega Prompt")
                edited_mega_prompt = st.text_area(
                    "Detailed section guidance used by notes generation",
                    value=topic.get("teaching_prompt", ""),
                    height=180,
                    key=f"lp_edit_{topic.get('id', 'topic')}_mega",
                )

                st.markdown("### Coverage & Continuity Controls")
                edited_prereq = st.text_area(
                    "Prerequisites (one per line)",
                    value=_bullets_to_text(coverage.get("prerequisites", [])),
                    height=90,
                    key=f"lp_edit_{topic.get('id', 'topic')}_prereq",
                )
                edited_include = st.text_area(
                    "Must Include (one per line)",
                    value=_bullets_to_text(coverage.get("must_include", [])),
                    height=90,
                    key=f"lp_edit_{topic.get('id', 'topic')}_include",
                )
                edited_exclude = st.text_area(
                    "Must Exclude / Avoid Repeating (one per line)",
                    value=_bullets_to_text(coverage.get("must_exclude", [])),
                    height=90,
                    key=f"lp_edit_{topic.get('id', 'topic')}_exclude",
                )
                edited_bridge = st.text_area(
                    "Bridge From Previous Section",
                    value=str(coverage.get("bridge_from_previous", "") or ""),
                    height=80,
                    key=f"lp_edit_{topic.get('id', 'topic')}_bridge",
                )
                edited_handoff = st.text_area(
                    "Handoff To Next Section",
                    value=str(coverage.get("handoff_to_next", "") or ""),
                    height=80,
                    key=f"lp_edit_{topic.get('id', 'topic')}_handoff",
                )

                save_clicked = st.form_submit_button("💾 Save Edits", type="primary")

            if save_clicked:
                topic["structured_content"] = edited_structured
                topic["teaching_prompt"] = edited_mega_prompt.strip()
                topic["coverage_contract"] = {
                    "prerequisites": _text_to_bullets(edited_prereq),
                    "must_include": _text_to_bullets(edited_include),
                    "must_exclude": _text_to_bullets(edited_exclude),
                    "bridge_from_previous": edited_bridge.strip(),
                    "handoff_to_next": edited_handoff.strip(),
                }
                topic["content"] = _build_topic_markdown(topic)

                # Lesson plan changed: invalidate downstream generated artifacts.
                st.session_state.detailed_notes_generated = False
                st.session_state.all_notes_data = []
                st.session_state.chapter_summaries = {}
                st.session_state.flashcards = []
                st.session_state.question_bank = []
                st.session_state.practice_exam = None
                st.session_state.glossary = []
                st.session_state.review_completed = False

                save_course_state()
                log_activity(
                    f"Lesson plan edited for {topic.get('id', 'topic')}",
                    icon="✏️", level="info",
                )
                st.success("Saved. Notes generation will now use these updated section instructions.")
                st.rerun()
