import streamlit as st
from datetime import timedelta

from Smart_Teaching_Assistance.ui.state_manager import (
    get_db, load_course_state, delete_course, reset_session, STEPS,
    save_course_state, set_generation_status, has_api_key,
)
from Smart_Teaching_Assistance.ui.components.header import render_api_config

def render_dashboard():
    # API Configuration Header
    render_api_config()
    
    # Hero Section
    st.markdown("""
        <div style="background: linear-gradient(90deg, #6366F1, #38BDF8); padding: 2.5rem; border-radius: 16px; margin-bottom: 2rem; color: white; box-shadow: 0 4px 15px rgba(99, 102, 241, 0.2);">
            <h1 style="color: white; margin: 0; font-size: 2.2rem;">🎓 Educator Command Center</h1>
            <p style="opacity: 0.95; margin: 0.5rem 0 0 0; font-size: 1.1rem;">Manage your AI-augmented courses and learning materials in one place.</p>
        </div>
    """, unsafe_allow_html=True)

    db = get_db()
    user_id = st.session_state.get("user_id")
    courses = db.fetchall(
        "SELECT id, subject_name, current_step, updated_at FROM courses WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,)
    )

    # Quick Stats
    cols = st.columns(3)
    with cols[0]:
        st.metric("Active Courses", len(courses))
    with cols[1]:
        completed = len([c for c in courses if c['current_step'] >= 5])
        st.metric("Published", completed)
    with cols[2]:
        active_tasks = _count_active_workers()
        if active_tasks:
            st.metric("Active Tasks", active_tasks, delta="In Progress")
        else:
            st.metric("System Load", "Optimal", delta="0ms latency")

    # ── Live progress for active workers (auto-refreshing) ───────────
    if _count_active_workers() > 0:
        _render_active_tasks()

    # ── Interruption recovery cards ──────────────────────────────────
    _render_recovery_cards()

    st.markdown("---")

    # Actions
    c1, c2 = st.columns([4, 1])
    with c1:
        st.markdown("### 📚 My Courses")
    with c2:
        if st.button("➕ Create New Course", type="primary", use_container_width=True):
            reset_session()
            st.session_state.step = 0
            st.rerun()

    if not courses:
        st.container(border=True).info("No saved courses yet. Click 'Create New Course' to get started!")
        return

    # Course Grid
    for c in courses:
        with st.container(border=True):
            col1, col2, col3 = st.columns([5, 2, 2])

            with col1:
                st.markdown(f"#### {c['subject_name'] or 'Untitled Course'}")
                st.caption(f"Last modified: {c['updated_at']}")

            with col2:
                step_idx = c['current_step']
                progress_val = (step_idx + 1) / len(STEPS) if step_idx >= 0 else 0
                st.progress(progress_val)
                label = STEPS[step_idx] if 0 <= step_idx < len(STEPS) else "Draft"
                st.caption(f"Status: **{label}**")

            with col3:
                btn_cols = st.columns([1, 1], gap="small")
                with btn_cols[0]:
                    if st.button("🚀 Resume", key=f"resume_{c['id']}", use_container_width=True):
                        if load_course_state(c['id']):
                            st.rerun()
                with btn_cols[1]:
                    if st.button("🗑️", key=f"del_{c['id']}", use_container_width=True):
                        delete_course(c['id'])
                        st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 Sync Workspace", use_container_width=True):
        st.rerun()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _count_active_workers() -> int:
    count = 0
    for key in ("notes_worker", "lp_worker"):
        w = st.session_state.get(key)
        if w and hasattr(w, "is_running") and w.is_running:
            count += 1
    return count


@st.fragment(run_every=timedelta(seconds=2))
def _render_active_tasks():
    """Auto-refreshing live-progress cards for every active background worker."""
    workers = [
        ("notes_worker", "📖 Notes Generation", 3),
        ("lp_worker", "📝 Lesson Plan", 2),
    ]
    any_active = False

    for key, label, target_step in workers:
        w = st.session_state.get(key)
        if w is None or not hasattr(w, "is_running"):
            continue

        is_active = w.is_running or w.status == "pausing"
        is_terminal = w.status in ("completed", "paused", "error")

        if not is_active and not is_terminal:
            continue

        any_active = True
        prog = w.progress
        frac = prog.get("frac", 0.0)
        completed = prog.get("completed", 0)
        total = prog.get("total", 0)
        message = prog.get("message", "")
        eta_str = prog.get("eta_str", "")
        elapsed_str = prog.get("elapsed_str", "")

        # Sync partial data
        if key == "notes_worker" and hasattr(w, "partial_notes"):
            partial = w.partial_notes
            if partial and len(partial) > len(st.session_state.get("all_notes_data", [])):
                st.session_state.all_notes_data = partial
        elif key == "lp_worker" and hasattr(w, "partial_plan"):
            from Smart_Teaching_Assistance.ui.components.common import count_tree
            partial = w.partial_plan
            if partial and partial.get("topics"):
                p_count = count_tree(partial.get("topics", []))
                c_count = count_tree(st.session_state.lesson_plan.get("topics", []))
                if p_count > c_count:
                    st.session_state.lesson_plan = partial

        if is_active:
            phase = prog.get("phase", "notes")
            with st.container(border=True):
                phase_label = f"{label} — Fetching Images" if phase == "images" else f"{label} in Progress"
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
                    f'<span style="font-size:1.3rem">{label.split()[0]}</span>'
                    f'<span style="font-weight:700;font-size:1.05rem">{phase_label}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.progress(frac)
                if phase == "images":
                    mc1, mc2, mc3 = st.columns(3)
                    mc1.metric("Notes", f"✅ {completed}/{total}")
                    mc2.metric("Elapsed", elapsed_str or "0s")
                    mc3.metric("Phase", "Downloading Images")
                else:
                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.metric("Progress", f"{completed}/{total}")
                    mc2.metric("Elapsed", elapsed_str or "0s")
                    mc3.metric("ETA", eta_str or "...")
                    mc4.metric("Status", "Pausing..." if w.status == "pausing" else "Running")
                if message:
                    st.caption(f"🔄 {message}")

                bc1, bc2, _ = st.columns([1, 1, 3])
                with bc1:
                    if w.status == "running" and st.button("⏸️ Pause", key=f"_dash_pause_{key}"):
                        w.pause()
                with bc2:
                    if st.button(f"→ Go to {label.split()[-1]}", key=f"_dash_goto_{key}"):
                        st.session_state.step = target_step
                        st.rerun(scope="app")

        # Terminal: trigger full rerun so page content refreshes
        if is_terminal and not st.session_state.get(f"_dash_terminal_{key}"):
            st.session_state[f"_dash_terminal_{key}"] = True
            st.rerun(scope="app")


def _render_recovery_cards():
    """Show recovery UI if generation_status indicates an interrupted task."""
    gen_status = st.session_state.get("generation_status", "idle")
    if gen_status == "idle":
        return

    # Notes recovery
    notes_worker = st.session_state.get("notes_worker")
    notes_running = notes_worker and hasattr(notes_worker, "is_running") and notes_worker.is_running
    if gen_status in ("generating_notes", "paused_notes") and not notes_running:
        completed = len(st.session_state.get("all_notes_data", []))
        with st.container(border=True):
            st.markdown("### ⚠️ Notes Generation Interrupted")
            st.markdown(
                f"Notes generation was interrupted with **{completed}** topics saved. "
                "Your progress is preserved."
            )
            rc1, rc2, _ = st.columns([1, 1, 3])
            with rc1:
                if st.button("🚀 Resume Notes", type="primary", key="_dash_resume_notes"):
                    st.session_state._auto_resume = "notes"
                    st.session_state.step = 3
                    st.rerun()
            with rc2:
                if st.button("🔄 Start Over", key="_dash_reset_notes"):
                    st.session_state.all_notes_data = []
                    st.session_state.detailed_notes_generated = False
                    set_generation_status("idle")
                    st.session_state.step = 3
                    st.rerun()

    # LP recovery
    lp_worker = st.session_state.get("lp_worker")
    lp_running = lp_worker and hasattr(lp_worker, "is_running") and lp_worker.is_running
    if gen_status in ("generating_lp", "paused_lp") and not lp_running:
        from Smart_Teaching_Assistance.ui.components.common import count_tree
        completed = count_tree(st.session_state.get("lesson_plan", {}).get("topics", []))
        with st.container(border=True):
            st.markdown("### ⚠️ Lesson Plan Generation Interrupted")
            st.markdown(
                f"Lesson plan generation was interrupted with **{completed}** topics saved. "
                "Your progress is preserved."
            )
            rc1, rc2, _ = st.columns([1, 1, 3])
            with rc1:
                if st.button("🚀 Resume Plan", type="primary", key="_dash_resume_lp"):
                    st.session_state._auto_resume = "lp"
                    st.session_state.step = 2
                    st.rerun()
            with rc2:
                if st.button("🔄 Start Over", key="_dash_reset_lp"):
                    st.session_state.lesson_plan = {}
                    st.session_state.lesson_plan_generated = False
                    set_generation_status("idle")
                    st.session_state.step = 2
                    st.rerun()


