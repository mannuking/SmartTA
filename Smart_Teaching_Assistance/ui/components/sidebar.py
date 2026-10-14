"""
Beautiful sidebar with visual pipeline, live pulse, collapsible sections,
and real-time status awareness.
"""

import streamlit as st
import traceback

try:
    from Smart_Teaching_Assistance.ui.state_manager import (
        STEPS, STEP_ICONS, go, reset_session, has_api_key, set_api_key,
        get_all_api_keys, add_api_key, remove_api_key,
    )
except (ImportError, ModuleNotFoundError):
    from state_manager import (
        STEPS, STEP_ICONS, go, reset_session, has_api_key, set_api_key,
        get_all_api_keys, add_api_key, remove_api_key,
    )

# ── Step status colours / icons ──────────────────────────────────────────────
_STATUS_MAP = {
    "done":     ("Done",    "✓", "#10B981", "sb-st-done"),
    "active":   ("Active",  "●", "#4F46E5", "sb-st-active"),
    "partial":  ("Partial", "◐", "#F59E0B", "sb-st-partial"),
    "optional": ("Skip OK", "○", "#94A3B8", "sb-st-optional"),
    "pending":  ("Pending", "○", "#94A3B8", "sb-st-pending"),
    "ready":    ("Ready",   "◉", "#06B6D4", "sb-st-ready"),
    "error":    ("Error",   "✗", "#EF4444", "sb-st-error"),
}


def _step_state(idx: int) -> str:
    """Return a status key for the given step index."""
    s = st.session_state
    cur = s.get("step", -1)
    err = s.get("sidebar_last_error", "")

    if idx == 0:
        if s.get("syllabus_text") and s.get("subject_name"):
            return "done"
        if s.get("syllabus_text") or s.get("subject_name"):
            return "partial"
        return "active" if cur == 0 else "pending"
    if idx == 1:
        if s.get("roadmap_generated"):
            return "done"
        return "active" if cur == 1 else "pending"
    if idx == 2:
        if s.get("lesson_plan_generated"):
            return "done"
        if s.get("lesson_plan"):
            return "partial"
        return "active" if cur == 2 else "pending"
    if idx == 3:
        if s.get("detailed_notes_generated"):
            return "done"
        if s.get("all_notes_data"):
            return "partial"
        if cur == 3 and err:
            return "error"
        return "active" if cur == 3 else "pending"
    if idx == 4:
        if s.get("personalized_versions"):
            return "done"
        return "active" if cur == 4 else "optional"
    if idx == 5:
        if s.get("detailed_notes_generated"):
            return "ready"
        return "pending"
    return "pending"


def _pct() -> int:
    s = st.session_state
    done = sum([
        bool(s.get("syllabus_text") and s.get("subject_name")),
        s.get("roadmap_generated", False),
        s.get("lesson_plan_generated", False),
        s.get("detailed_notes_generated", False),
        len(s.get("personalized_versions", [])) > 0,
    ])
    return int(done / 5 * 100)


# ── Public entry ─────────────────────────────────────────────────────────────
def render_sidebar():
    try:
        _build()
    except Exception as exc:
        with st.sidebar:
            st.error(f"Sidebar crashed: {exc}")
            st.code(traceback.format_exc(), language="python")
            if st.button("🔄 Reset", key="sb_err_reset"):
                reset_session()
                st.rerun()





def _build():
    with st.sidebar:
        # ── Brand ────────────────────────────────────────────────────
        st.markdown(
            '<div class="sb-brand">'
            '<span class="sb-logo">🎓</span>'
            '<div class="sb-brand-text">'
            '<span class="sb-title">Smart TA</span>'
            '<span class="sb-subtitle">AI Teaching Assistant</span>'
            '</div></div>',
            unsafe_allow_html=True,
        )

        # ── User Profile ─────────────────────────────────────────────
        username = st.session_state.get("username", "User")
        with st.container(border=True):
            cols = st.columns([1, 3])
            cols[0].markdown(f"### 👤")
            with cols[1]:
                st.markdown(f"**{username}**")
                from Smart_Teaching_Assistance.ui.components.auth import logout
                if st.button("🚪 Logout", key="sb_logout"):
                    logout()


        if not has_api_key():
            # Stop here if no API key is configured
            st.markdown('<div style="height:200px"></div>', unsafe_allow_html=True)
            _render_footer()
            return

        # ── Visual Pipeline ──────────────────────────────────────────
        pct = _pct()
        # ring SVG
        ring = (
            f'<svg viewBox="0 0 36 36" class="sb-ring">'
            f'<path class="sb-ring-track" d="M18 2.0845a15.9155 15.9155 0 010 31.831 15.9155 15.9155 0 010-31.831"/>'
            f'<path class="sb-ring-fill" stroke-dasharray="{pct},100" '
            f'd="M18 2.0845a15.9155 15.9155 0 010 31.831 15.9155 15.9155 0 010-31.831"/>'
            f'<text x="18" y="20.5" class="sb-ring-pct">{pct}%</text></svg>'
        )

        # Build the step pipeline cards
        pipeline_html = ""
        for i, (label, icon) in enumerate(zip(STEPS, STEP_ICONS)):
            state = _step_state(i)
            _, status_icon, color, cls = _STATUS_MAP[state]
            cur = st.session_state.get("step", -1)
            is_active = cur == i
            active_cls = "sb-pipe-active" if is_active else ""
            connector = '<div class="sb-connector"><div class="sb-conn-line"></div></div>' if i < 5 else ""

            pipeline_html += (
                f'<div class="sb-pipe-item {cls} {active_cls}" data-step="{i}">'
                f'<div class="sb-pipe-icon" style="border-color:{color}">'
                f'<span class="sb-pipe-emoji">{icon}</span>'
                f'<span class="sb-pipe-check" style="background:{color}">{status_icon}</span>'
                f'</div>'
                f'<div class="sb-pipe-info">'
                f'<span class="sb-pipe-label">{label}</span>'
                f'<span class="sb-pipe-status" style="color:{color}">{_STATUS_MAP[state][0]}</span>'
                f'</div></div>'
                f'{connector}'
            )

        st.markdown(
            f'<div class="sb-pipeline-wrap">'
            f'<div class="sb-pipeline-header">'
            f'<div class="sb-ring-wrap">{ring}</div>'
            f'<div class="sb-pipeline-title">Pipeline Progress</div>'
            f'</div>'
            f'<div class="sb-pipeline">{pipeline_html}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Navigation buttons (functional — the HTML above is visual only)
        with st.container():
            st.markdown('<div class="sb-nav-section">', unsafe_allow_html=True)
            if st.button("🏠 Dashboard", use_container_width=True,
                         type="primary" if st.session_state.get("step") == -1 else "secondary",
                         key="sb_dash"):
                go(-1); st.rerun()
            cols = st.columns(2)
            for i, (label, icon) in enumerate(zip(STEPS, STEP_ICONS)):
                col = cols[i % 2]
                state = _step_state(i)
                is_active = st.session_state.get("step") == i
                with col:
                    st.button(
                        f"{icon} {label.split()[0]}",
                        key=f"sb_{i}",
                        use_container_width=True,
                        type="primary" if is_active else "secondary",
                        on_click=go, args=(i,),
                    )
            st.markdown('</div>', unsafe_allow_html=True)

        # ── LIVE PULSE — always visible when a task is running ──────
        task = st.session_state.get("sidebar_current_task", "")
        last_err = st.session_state.get("sidebar_last_error", "")

        # Check background workers for real-time progress (even on other pages)
        frac = 0.0; plabel = ""; eta = ""; elapsed = ""
        _found_active = False
        for _wkey, _wlabel in [("notes_worker", "Detailed Notes"), ("lp_worker", "Lesson Plan")]:
            _w = st.session_state.get(_wkey)
            if _w is not None and hasattr(_w, "is_running") and _w.is_running:
                prog = _w.progress
                task = _wlabel
                frac = prog.get("frac", 0.0)
                plabel = prog.get("label", "")
                eta = prog.get("eta_str", "")
                elapsed = prog.get("elapsed_str", "")
                _found_active = True
                break
        if not _found_active:
            frac = st.session_state.get("sidebar_progress_frac", 0.0)
            plabel = st.session_state.get("sidebar_progress_label", "")
            eta = st.session_state.get("sidebar_eta", "")
            elapsed = st.session_state.get("sidebar_elapsed", "")

        if task:
            bar_w = f"{max(frac * 100, 2):.0f}%"

            stat_spans = ""
            if plabel:
                stat_spans += f'<span class="sb-pulse-stat">📊 {plabel}</span>'
            if elapsed:
                stat_spans += f'<span class="sb-pulse-stat">⏱ {elapsed}</span>'
            if eta:
                stat_spans += f'<span class="sb-pulse-stat">⏳ {eta}</span>'

            st.markdown(
                f'<div class="sb-pulse">'
                f'<div class="sb-pulse-dot"></div>'
                f'<div class="sb-pulse-body">'
                f'<div class="sb-pulse-title">{task}</div>'
                f'<div class="sb-pulse-bar-track">'
                f'<div class="sb-pulse-bar-fill" style="width:{bar_w}"></div></div>'
                f'<div class="sb-pulse-stats">{stat_spans}</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        elif last_err:
            st.markdown(
                f'<div class="sb-alert-card sb-alert-error">'
                f'<div class="sb-alert-icon">⚠️</div>'
                f'<div class="sb-alert-body">'
                f'<div class="sb-alert-title">Last Error</div>'
                f'<div class="sb-alert-msg">{last_err[:180]}</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

        # ── Course Card ─────────────────────────────────────────────
        subj = st.session_state.get("subject_name", "")
        if subj:
            diff = st.session_state.get("difficulty", "")
            topics_n = len(st.session_state.get("all_notes_data", []))
            fc_n = len(st.session_state.get("flashcards", []))
            qb_n = len(st.session_state.get("question_bank", []))
            gl_n = len(st.session_state.get("glossary", []))

            badges = ""
            for done_check, emoji, lbl in [
                (st.session_state.get("syllabus_text"), "✅", "Syllabus"),
                (st.session_state.get("roadmap_generated"), "🗺️", "Roadmap"),
                (st.session_state.get("lesson_plan_generated"), "📝", "Plan"),
                (topics_n, "📖", f"{topics_n} notes"),
                (fc_n, "🃏", f"{fc_n} cards"),
                (qb_n, "❓", f"{qb_n} Qs"),
                (gl_n, "📚", f"{gl_n} terms"),
            ]:
                if done_check:
                    badges += f'<span class="sb-badge">{emoji} {lbl}</span>'

            diff_html = f'<span class="sb-course-diff">🎯 {diff.title()}</span>' if diff else ""

            st.markdown(
                f'<div class="sb-course">'
                f'<div class="sb-course-header">'
                f'<span class="sb-course-icon">📚</span>'
                f'<div><div class="sb-course-name">{subj}</div>{diff_html}</div></div>'
                f'<div class="sb-badge-row">{badges}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Activity Log (collapsible) ──────────────────────────────
        log = st.session_state.get("activity_log", [])
        if log:
            with st.expander(f"📋 Activity Log ({len(log)})", expanded=False):
                for entry in log[:20]:
                    lvl = entry.get("level", "info")
                    color_map = {"success": "#10B981", "error": "#EF4444",
                                 "warning": "#F59E0B", "progress": "#4F46E5", "info": "#94A3B8"}
                    c = color_map.get(lvl, "#94A3B8")
                    st.markdown(
                        f'<div style="display:flex;gap:6px;font-size:0.72rem;'
                        f'padding:3px 0;border-bottom:1px solid #f1f5f9">'
                        f'<span style="color:#94a3b8;font-family:monospace;min-width:42px">'
                        f'{entry.get("ts","")}</span>'
                        f'<span>{entry.get("icon","")}</span>'
                        f'<span style="color:{c}">{entry.get("msg","")}</span></div>',
                        unsafe_allow_html=True,
                    )

        # ── Debug Inspector (collapsible) ────────────────────────────
        with st.expander("🔍 Debug Inspector", expanded=False):
            debug = {
                "Step": st.session_state.get("step", -1),
                "Subject": st.session_state.get("subject_name", "—"),
                "PDF": "✅" if st.session_state.get("pdf_uploaded") else "❌",
                "Roadmap": "✅" if st.session_state.get("roadmap_generated") else "❌",
                "Lesson Plan": "✅" if st.session_state.get("lesson_plan_generated") else "❌",
                "Notes": "✅" if st.session_state.get("detailed_notes_generated") else "❌",
                "Reviewed": "✅" if st.session_state.get("review_completed") else "❌",
                "Course ID": st.session_state.get("course_id", "—"),
            }
            for k, v in debug.items():
                st.markdown(f"**{k}:** `{v}`")

            st.markdown("---")
            sizes = {
                "Syllabus": len(st.session_state.get("syllabus_text", "")),
                "PDF": len(st.session_state.get("pdf_text", "")),
                "Notes MD": len(st.session_state.get("notes_markdown", "")),
                "Topics": len(st.session_state.get("all_notes_data", [])),
            }
            for k, v in sizes.items():
                if v:
                    st.caption(f"{k}: {v:,}" if isinstance(v, int) else f"{k}: {v}")

        _render_footer()


def _render_footer():
    """Render the sidebar footer with reset/clear buttons.

    Reset  : wipe the in-memory session AND navigate back to the
             Dashboard (step=-1). From the Dashboard the user can
             "Create New Course" to start a fresh pipeline.
    Clear  : wipe just the sidebar activity log + last-error banner.
             Keeps the current step / course intact (useful when
             you just want a clean log feed).
    """
    st.markdown('<div class="sb-footer">', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            "🔄 Reset",
            use_container_width=True,
            key="sb_reset",
            help="Clear the current course and return to the Dashboard so you can start a new pipeline from Step 1.",
        ):
            reset_session()
            st.session_state["step"] = -1   # back to Dashboard
            st.rerun()
    with c2:
        if st.button(
            "🧹 Clear",
            use_container_width=True,
            key="sb_clear",
            help="Just clear the sidebar activity log and error banner. Keeps the current course loaded.",
        ):
            st.session_state.activity_log = []
            st.session_state.sidebar_last_error = ""
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
