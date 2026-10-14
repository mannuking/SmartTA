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

        # ── User Profile (compact) ─────────────────────────────────
        username = st.session_state.get("username", "User")
        with st.container(border=True):
            cols = st.columns([1, 3])
            cols[0].markdown(f"### 👤")
            with cols[1]:
                st.markdown(f"**{username}**")
                from Smart_Teaching_Assistance.ui.components.auth import logout
                if st.button("🚪 Logout", key="sb_logout", use_container_width=True):
                    logout()

        if not has_api_key():
            st.markdown('<div style="height:200px"></div>', unsafe_allow_html=True)
            _render_footer()
            return

        # ── Visual Pipeline (clean, one row per step) ──────────────
        pct = _pct()
        # Ring with progress
        ring = (
            f'<svg viewBox="0 0 36 36" class="sb-ring">'
            f'<path class="sb-ring-track" d="M18 2.0845a15.9155 15.9155 0 010 31.831 15.9155 15.9155 0 010-31.831"/>'
            f'<path class="sb-ring-fill" stroke-dasharray="{pct},100" '
            f'd="M18 2.0845a15.9155 15.9155 0 010 31.831 15.9155 15.9155 0 010-31.831"/>'
            f'<text x="18" y="20.5" class="sb-ring-pct">{pct}%</text></svg>'
        )

        pipeline_html = ""
        for i, (label, icon) in enumerate(zip(STEPS, STEP_ICONS)):
            state = _step_state(i)
            _, status_icon, color, cls = _STATUS_MAP[state]
            cur = st.session_state.get("step", -1)
            is_active = cur == i
            active_cls = "sb-pipe-active" if is_active else ""
            connector = (
                '<div class="sb-connector"><div class="sb-conn-line"></div></div>'
                if i < 5 else ""
            )

            pipeline_html += (
                f'<div class="sb-pipe-item {cls} {active_cls}" data-step="{i}">'
                f'<div class="sb-pipe-icon" style="border-color:{color}">'
                f'<span class="sb-pipe-emoji">{icon}</span>'
                f'<span class="sb-pipe-check" style="background:{color}">{status_icon}</span>'
                f'</div>'
                f'<div class="sb-pipe-info">'
                f'<span class="sb-pipe-label">{label}</span>'
                f'<span class="sb-pipe-status" style="color:{color}">'
                f'{_STATUS_MAP[state][0]}</span>'
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

        # ── LIVE PROGRESS — always visible when something is running ──
        # Reads from BOTH the background-worker registry AND the
        # sidebar_progress_* session keys. Earlier code only read the
        # latter, so when a background worker was running, the pulse
        # bar showed nothing -- the user saw a 0% bar that disappeared.
        _render_live_progress()

        # ── Course Card with action buttons ──────────────────────
        subj = st.session_state.get("subject_name", "")
        if subj:
            _render_course_card(subj)

        # ── Activity Log (collapsible) ──────────────────────────────
        log = st.session_state.get("activity_log", [])
        if log:
            with st.expander(f"📋 Activity Log ({len(log)})", expanded=False):
                for entry in log[:15]:
                    lvl = entry.get("level", "info")
                    color_map = {
                        "success": "#10B981", "error": "#EF4444",
                        "warning": "#F59E0B", "progress": "#4F46E5",
                        "info": "#94A3B8",
                    }
                    c = color_map.get(lvl, "#94A3B8")
                    st.markdown(
                        f'<div style="display:flex;gap:6px;font-size:0.72rem;'
                        f'padding:3px 0;border-bottom:1px solid #f1f5f9">'
                        f'<span style="color:#94a3b8;font-family:monospace;'
                        f'min-width:42px">{entry.get("ts", "")}</span>'
                        f'<span>{entry.get("icon", "")}</span>'
                        f'<span style="color:{c}">{entry.get("msg", "")}</span></div>',
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
                "Course ID": st.session_state.get("course_id", "—"),
            }
            for k, v in debug.items():
                st.markdown(f"**{k}:** `{v}`")

            sizes = {
                "Syllabus": len(st.session_state.get("syllabus_text", "")),
                "PDF text": len(st.session_state.get("pdf_text", "")),
                "Notes MD": len(st.session_state.get("notes_markdown", "")),
                "Topics": len(st.session_state.get("all_notes_data", [])),
            }
            for k, v in sizes.items():
                if v:
                    st.caption(f"{k}: {v:,}" if isinstance(v, int) else f"{k}: {v}")

        _render_device_toggle()
        _render_footer()


def _render_live_progress():
    """Live progress card. Pulls from BOTH the background-worker
    registry and the sidebar_progress_* session keys.

    Old behavior: only read sidebar_progress_frac, which was only
    written by step-level update_sidebar_progress() callbacks. When
    the lesson-plan or notes BACKGROUND WORKER was running, the
    progress bar was empty (0%) or invisible -- the user had no
    feedback. Now we ALSO read the worker.progress dict directly,
    so as long as either source has a fraction > 0, the bar fills.
    """
    last_err = st.session_state.get("sidebar_last_error", "")

    # 1) Check background workers first (this is the real-time source)
    frac = 0.0
    plabel = ""
    eta = ""
    elapsed = ""
    task = ""
    found_active = False
    for wkey, wlabel in [
        ("lp_worker", "Lesson Plan"),
        ("notes_worker", "Detailed Notes"),
    ]:
        w = st.session_state.get(wkey)
        if w is not None and hasattr(w, "is_running") and w.is_running:
            prog = w.progress
            frac = float(prog.get("frac", 0.0))
            plabel = str(prog.get("label") or prog.get("message") or "")
            eta = str(prog.get("eta_str") or "")
            elapsed = str(prog.get("elapsed_str") or "")
            task = wlabel
            found_active = True
            break

    # 2) Fall back to sidebar_progress_* (set by step callbacks)
    if not found_active:
        frac = max(frac, float(st.session_state.get("sidebar_progress_frac", 0.0)))
        plabel = plabel or str(st.session_state.get("sidebar_progress_label", ""))
        eta = eta or str(st.session_state.get("sidebar_eta", ""))
        elapsed = elapsed or str(st.session_state.get("sidebar_elapsed", ""))
        task = st.session_state.get("sidebar_current_task", "")

    # 3) Render
    if task or found_active:
        bar_w = f"{max(frac * 100, 4):.0f}%"
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
            f'<div class="sb-pulse-title">{task or "Working"}</div>'
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


def _render_course_card(subj: str):
    """Course summary card with quick-action buttons.

    The OLD version was just a name + badges. No actions. The user
    had to scroll the pipeline nav at the top to jump around. The
    NEW version puts the most common actions right on the card:
    go to the current step, jump to Export, or copy the subject name.
    """
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
        (st.session_state.get("detailed_notes_generated"), "📖", "Notes"),
        (fc_n, "🃏", f"{fc_n} cards"),
        (qb_n, "❓", f"{qb_n} Qs"),
        (gl_n, "📚", f"{gl_n} terms"),
    ]:
        if done_check:
            badges += f'<span class="sb-badge">{emoji} {lbl}</span>'

    diff_html = (
        f'<span class="sb-course-diff">🎯 {diff.title()}</span>' if diff else ""
    )

    st.markdown(
        f'<div class="sb-course">'
        f'<div class="sb-course-header">'
        f'<span class="sb-course-icon">📚</span>'
        f'<div><div class="sb-course-name">{subj}</div>{diff_html}</div></div>'
        f'<div class="sb-badge-row">{badges}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Quick-action buttons: jump to the right step. Disabled if the
    # prerequisite isn't met.
    cur_step = st.session_state.get("step", -1)
    has_syllabus = bool(st.session_state.get("syllabus_text") and subj)
    has_roadmap = bool(st.session_state.get("roadmap_generated"))
    has_lp = bool(st.session_state.get("lesson_plan_generated"))
    has_notes = bool(st.session_state.get("detailed_notes_generated"))

    # Two rows of three, each is a quick-jump.
    r1c1, r1c2, r1c3 = st.columns(3)
    with r1c1:
        if st.button(
            "📋 Input",
            key="qa_input",
            use_container_width=True,
            type="primary" if cur_step == 0 else "secondary",
            disabled=cur_step == 0,
        ):
            go(0); st.rerun()
    with r1c2:
        if st.button(
            "🗺️ Plan",
            key="qa_roadmap",
            use_container_width=True,
            type="primary" if cur_step == 1 else "secondary",
            disabled=not has_syllabus or cur_step == 1,
            help="Generate a roadmap" if not has_syllabus else "Open Roadmap step",
        ):
            go(1); st.rerun()
    with r1c3:
        if st.button(
            "📝 LP",
            key="qa_lp",
            use_container_width=True,
            type="primary" if cur_step == 2 else "secondary",
            disabled=not has_roadmap or cur_step == 2,
            help="Generate a roadmap first" if not has_roadmap else "Open Lesson Plan step",
        ):
            go(2); st.rerun()

    r2c1, r2c2, r2c3 = st.columns(3)
    with r2c1:
        if st.button(
            "📖 Notes",
            key="qa_notes",
            use_container_width=True,
            type="primary" if cur_step == 3 else "secondary",
            disabled=not has_lp or cur_step == 3,
            help="Generate a lesson plan first" if not has_lp else "Open Notes step",
        ):
            go(3); st.rerun()
    with r2c2:
        if st.button(
            "🎯 Adapt",
            key="qa_personalize",
            use_container_width=True,
            type="primary" if cur_step == 4 else "secondary",
            disabled=not has_notes or cur_step == 4,
            help="Generate notes first" if not has_notes else "Open Personalize step",
        ):
            go(4); st.rerun()
    with r2c3:
        if st.button(
            "📤 Export",
            key="qa_export",
            use_container_width=True,
            type="primary" if cur_step == 5 else "secondary",
            disabled=not has_notes or cur_step == 5,
            help="Generate notes first" if not has_notes else "Open Export step",
        ):
            go(5); st.rerun()


def _render_device_toggle():
    """CPU / GPU toggle for the local ML models (YOLO, BGE-M3, CLIP, reranker).

    Setting persists in:
      - os.environ['SMARTTA_DEVICE']  (consumed by the embedders + YOLO)
      - st.session_state['device']    (consumed by the UI itself)

    Flipping the toggle drops the cached embedders so the next ingest
    or query reloads on the new device. We don't try to live-move
    already-loaded CUDA tensors to CPU or vice versa -- that needs a
    full process restart for stable behavior.
    """
    import os
    import torch

    cuda_ok = False
    try:
        cuda_ok = bool(torch.cuda.is_available())
        if cuda_ok:
            gpu_name = torch.cuda.get_device_name(0)
        else:
            gpu_name = ""
    except Exception:
        gpu_name = ""

    current = st.session_state.get("device") or os.environ.get("SMARTTA_DEVICE", "")
    if not current:
        current = "cuda" if cuda_ok else "cpu"

    st.markdown("---")
    st.markdown("##### ⚙️ Compute Device")
    if cuda_ok:
        st.caption(f"GPU available: {gpu_name}")
    else:
        st.caption("No CUDA GPU detected — CPU only.")

    # Two-button row
    c1, c2 = st.columns(2)
    with c1:
        cpu_active = (current == "cpu")
        if st.button(
            "🖥️ CPU",
            use_container_width=True,
            type="primary" if cpu_active else "secondary",
            key="sb_dev_cpu",
            help="Force CPU for YOLO + all embedding models. Most stable. Recommended default.",
        ):
            os.environ["SMARTTA_DEVICE"] = "cpu"
            st.session_state["device"] = "cpu"
            # Drop cached models so the next call reloads on CPU
            try:
                from Smart_Teaching_Assistance.rag.embeddings import reset_caches
                reset_caches()
            except Exception:
                pass
            st.rerun()
    with c2:
        gpu_disabled = not cuda_ok
        gpu_active = (current == "cuda") and cuda_ok
        if st.button(
            "🚀 GPU",
            use_container_width=True,
            type="primary" if gpu_active else "secondary",
            disabled=gpu_disabled,
            key="sb_dev_gpu",
            help=("Force CUDA for YOLO + all embedding models. Faster for large PDFs and "
                  "long RAG queries, but BGE-M3 + CLIP need ~2GB VRAM. NOTE: YOLO detection "
                  "on single images is actually slower on small GPUs (launch overhead > "
                  "compute) -- only the embedding models reliably benefit from CUDA."),
        ):
            os.environ["SMARTTA_DEVICE"] = "cuda"
            st.session_state["device"] = "cuda"
            try:
                from Smart_Teaching_Assistance.rag.embeddings import reset_caches
                reset_caches()
            except Exception:
                pass
            st.rerun()

    # Show the active device + VRAM
    if cuda_ok:
        try:
            vram_used = torch.cuda.memory_allocated() / 1024**3
            vram_total = torch.cuda.get_device_properties(0).total_memory / 1024**3
            st.caption(
                f"Active: **{current.upper()}** · VRAM: "
                f"{vram_used:.1f} / {vram_total:.1f} GB"
            )
        except Exception:
            st.caption(f"Active: **{current.upper()}**")
    else:
        st.caption(f"Active: **{current.upper()}**")


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
            # reset_session() sets _user_bypassed_autoload=True, so
            # the next render's autoload check bails and the user
            # actually sees an empty Dashboard instead of the most
            # recent course re-loading behind their back.
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
