import sys
import os
from pathlib import Path

# MANDATORY: Add project root to sys.path BEFORE any local imports
current_file = Path(__file__).resolve()
# parents[0]=ui, parents[1]=Smart_Teaching_Assistance, parents[2]=project root
root_dir = str(current_file.parents[2])
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import streamlit as st
import logging
from datetime import timedelta

# Suppress noisy Streamlit fragment-timer warnings when fragments are
# destroyed by full-app reruns.  These are harmless cosmetic log messages.
logging.getLogger("streamlit.runtime.scriptrunner.script_runner").setLevel(logging.ERROR)

try:
    from Smart_Teaching_Assistance.ui.state_manager import init_state, get_db, has_api_key
    from Smart_Teaching_Assistance.ui.components.common import load_css, render_progress
    from Smart_Teaching_Assistance.ui.components.sidebar import render_sidebar
    from Smart_Teaching_Assistance.ui.components.dashboard import render_dashboard
    from Smart_Teaching_Assistance.ui.steps import step_input, step_roadmap, step_lesson_plan, step_detailed_notes, step_personalize, step_export
except (ImportError, ModuleNotFoundError):
    # Fallback for some Streamlit environments
    from state_manager import init_state, get_db, has_api_key
    from components.common import load_css, render_progress
    from components.sidebar import render_sidebar
    from components.dashboard import render_dashboard
    from steps import step_input, step_roadmap, step_lesson_plan, step_detailed_notes, step_personalize, step_export
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    st.set_page_config(
        page_title="Smart Teaching Assistant",
        page_icon="🎓",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    
    # Initialize session state & CSS
    load_css()
    init_state()

    # Authentication Gate
    if st.session_state.get("user_id") is None:
        from Smart_Teaching_Assistance.ui.components.auth import render_auth
        render_auth()
        st.stop()

    # Sidebar is always visible
    try:
        render_sidebar()
    except Exception as e:
        st.sidebar.error(f"Sidebar error: {e}")
        import traceback
        st.sidebar.code(traceback.format_exc())

    # Rate-limit recovery banner
    _rate_limit_banner()

    # API key gate — block workflow steps until a key is provided
    if not has_api_key() and st.session_state.step != -1:
        st.warning(
            "🔑 **API key required.** Enter your Gemini API key on the Dashboard to continue.",
            icon="⚠️",
        )
        st.stop()

    # Global live-progress banner (only when workers are active)
    if _has_active_workers():
        _global_worker_banner()

    # Routing logic
    if st.session_state.step == -1:
        render_dashboard()
    else:
        render_progress()
        
        step_fns = [
            step_input, 
            step_roadmap, 
            step_lesson_plan, 
            step_detailed_notes, 
            step_personalize, 
            step_export
        ]
        
        if 0 <= st.session_state.step < len(step_fns):
            step_fns[st.session_state.step]()
        else:
            # Fallback to dashboard if step is invalid
            st.session_state.step = -1
            st.rerun()

    # Sync background worker → session state (handles completion on any page)
    _maybe_background_refresh()


def _rate_limit_banner():
    """Show a persistent, friendly banner when generation was paused due to rate limits."""
    gen_status = st.session_state.get("generation_status", "idle")
    last_err = st.session_state.get("sidebar_last_error", "")
    is_rate_limited = (
        "rate limit" in last_err.lower()
        or "429" in last_err
        or "quota" in last_err.lower()
    )
    is_paused = gen_status in ("paused_notes", "paused_lp")

    if not (is_rate_limited or (is_paused and "rate" in last_err.lower())):
        return

    st.markdown(
        '<div style="background:linear-gradient(90deg,#FBBF24,#F59E0B);'
        'padding:16px 20px;border-radius:12px;margin-bottom:16px;'
        'display:flex;align-items:center;gap:12px">'
        '<span style="font-size:1.8rem">⏳</span>'
        '<div>'
        '<div style="font-weight:700;font-size:1.05rem;color:#78350F">'
        'Rate Limit Reached — Your Progress Is Saved!</div>'
        '<div style="color:#92400E;font-size:0.88rem;margin-top:2px">'
        'The Gemini API free tier has usage limits. '
        'Wait 1-2 minutes, then click <b>Resume</b> on the dashboard or in the relevant step. '
        'All your generated content is safely stored.'
        '</div></div></div>',
        unsafe_allow_html=True,
    )


def _has_active_workers() -> bool:
    """Return True if any background worker is active or in a terminal state."""
    for key in ("notes_worker", "lp_worker"):
        w = st.session_state.get(key)
        if w is None or not hasattr(w, "is_running"):
            continue
        if w.is_running or w.status in ("pausing", "completed", "paused", "error"):
            return True
    return False


# ── Global live-progress banner (auto-refreshing fragment) ───────────────────

@st.fragment(run_every=timedelta(seconds=2))
def _global_worker_banner():
    """Auto-refreshing banner shown on every page EXCEPT the page that owns the worker.

    Step 3 (Notes) and Step 2 (Lesson Plan) have their own detailed fragments.
    On all other pages this renders a compact progress card per active worker.
    """
    current_step = st.session_state.get("step")

    workers = [
        ("notes_worker", "📖 Notes Generation", 3, "notes"),
        ("lp_worker", "📝 Lesson Plan", 2, "lp"),
    ]

    any_terminal = False

    for key, label, owner_step, short in workers:
        # Skip if on the dashboard (it has its own _render_active_tasks fragment)
        # or if on the page that owns this worker (it handles its own display)
        if current_step == -1 or current_step == owner_step:
            continue

        worker = st.session_state.get(key)
        if worker is None or not hasattr(worker, "is_running"):
            continue

        is_active = worker.is_running or worker.status == "pausing"
        is_terminal = worker.status in ("completed", "paused", "error")

        if not is_active and not is_terminal:
            continue

        # Sync partial data on every tick
        if key == "notes_worker" and hasattr(worker, "partial_notes"):
            partial = worker.partial_notes
            if partial and len(partial) > len(st.session_state.get("all_notes_data", [])):
                st.session_state.all_notes_data = partial
        elif key == "lp_worker" and hasattr(worker, "partial_plan"):
            from Smart_Teaching_Assistance.ui.components.common import count_tree
            partial = worker.partial_plan
            if partial and partial.get("topics"):
                p_count = count_tree(partial.get("topics", []))
                c_count = count_tree(st.session_state.lesson_plan.get("topics", []))
                if p_count > c_count:
                    st.session_state.lesson_plan = partial

        # Render compact banner while running
        if is_active:
            prog = worker.progress
            phase = prog.get("phase", "notes")
            frac = prog.get("frac", 0.0)
            completed = prog.get("completed", 0)
            total = prog.get("total", 0)
            message = prog.get("message", "")
            eta_str = prog.get("eta_str", "")
            elapsed_str = prog.get("elapsed_str", "")

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
                    mc4.metric("Status", "Pausing..." if worker.status == "pausing" else "Running")
                if message:
                    st.caption(f"🔄 {message}")

                bc1, bc2, _ = st.columns([1, 1, 3])
                with bc1:
                    if worker.status == "running" and st.button("⏸️ Pause", key=f"_gbl_pause_{short}"):
                        worker.pause()
                with bc2:
                    if st.button(f"→ Go to {label.split()[-1]}", key=f"_gbl_goto_{short}"):
                        st.session_state.step = owner_step
                        st.rerun(scope="app")

        # Terminal: one full rerun so sidebar + page content refresh
        if is_terminal:
            any_terminal = True

    if any_terminal and not st.session_state.get("_global_terminal_rerun"):
        st.session_state._global_terminal_rerun = True
        st.rerun(scope="app")


# ── Background sync (runs once per full page render) ─────────────────────────

def _maybe_background_refresh():
    """Sync background-worker state into session on every page render.

    Handles both notes_worker and lp_worker:
      - Running: keep sidebar vars current.
      - Completed: harvest final results, reset worker.
      - Paused / Error: persist partial data, clear sidebar.
    """
    _sync_notes_worker()
    _sync_lp_worker()


def _sync_notes_worker():
    """Sync notes_worker state into session."""
    worker = st.session_state.get("notes_worker")
    if worker is None or not hasattr(worker, "is_running"):
        return
    if not worker.is_running and worker.status not in ("completed", "paused", "error"):
        return

    prog = worker.progress

    # Sync partial notes
    if hasattr(worker, "partial_notes"):
        partial = worker.partial_notes
        if partial and len(partial) > len(st.session_state.get("all_notes_data", [])):
            st.session_state.all_notes_data = partial

    if worker.is_running:
        st.session_state.sidebar_current_task = "Detailed Notes"
        st.session_state.sidebar_progress_frac = prog.get("frac", 0.0)
        st.session_state.sidebar_progress_label = prog.get("label", "")
        st.session_state.sidebar_eta = prog.get("eta_str", "")
        st.session_state.sidebar_elapsed = prog.get("elapsed_str", "")
        return

    if worker.status == "completed":
        results = worker.results
        if results:
            try:
                from Smart_Teaching_Assistance.ui.steps._shared import (
                    get_content_generator, save_course_state, count_tree, log_activity,
                )
            except ImportError:
                from steps._shared import get_content_generator, save_course_state, count_tree, log_activity

            all_notes, answer_key = results
            ContentGenerator = get_content_generator()
            st.session_state.all_notes_data = all_notes
            st.session_state.answer_key_text = answer_key
            st.session_state.notes_markdown = ContentGenerator.elements_to_markdown(
                all_notes, answer_key
            )
            st.session_state.glossary = ContentGenerator.extract_glossary(all_notes)

            total_needed = count_tree(
                st.session_state.get("lesson_plan", {}).get("topics", [])
            )
            if len(all_notes) >= total_needed:
                st.session_state.detailed_notes_generated = True
            save_course_state()

            total_imgs = sum(
                1 for n in all_notes for e in n.get("elements", [])
                if e.get("type") == "image_placeholder"
            )
            fetched_imgs = sum(
                1 for n in all_notes for e in n.get("elements", [])
                if e.get("type") == "image_placeholder"
                and e.get("image_local_path")
                and os.path.isfile(e.get("image_local_path", ""))
            )
            img_msg = f" | 🖼️ {fetched_imgs}/{total_imgs} images" if total_imgs else ""
            log_activity(
                f"Notes completed: {len(all_notes)} topics{img_msg}",
                icon="✅", level="success",
            )

        from Smart_Teaching_Assistance.ui.state_manager import clear_sidebar_progress, set_generation_status
        set_generation_status("idle")
        clear_sidebar_progress()
        from Smart_Teaching_Assistance.ui.background_worker import BackgroundNotesWorker, unregister_worker
        cid = st.session_state.get("course_id")
        if cid is not None:
            unregister_worker(cid, "notes")
        st.session_state.notes_worker = BackgroundNotesWorker()
        st.session_state.pop("_global_terminal_rerun", None)
        return

    if worker.status in ("paused", "error"):
        try:
            from Smart_Teaching_Assistance.ui.steps._shared import (
                save_course_state, log_activity, log_error,
            )
        except ImportError:
            from steps._shared import save_course_state, log_activity, log_error

        partial = worker.partial_notes
        if partial:
            st.session_state.all_notes_data = partial
            save_course_state()

        from Smart_Teaching_Assistance.ui.state_manager import clear_sidebar_progress, set_generation_status
        if worker.status == "paused":
            set_generation_status("paused_notes")
            log_activity("Notes paused", icon="⏸️", level="warning")
        else:
            set_generation_status("idle")
            log_error(f"Notes error: {prog.get('error', 'Unknown')}")
            from Smart_Teaching_Assistance.ui.background_worker import unregister_worker
            cid = st.session_state.get("course_id")
            if cid is not None:
                unregister_worker(cid, "notes")

        clear_sidebar_progress()
        st.session_state.pop("_global_terminal_rerun", None)


def _sync_lp_worker():
    """Sync lp_worker state into session."""
    worker = st.session_state.get("lp_worker")
    if worker is None or not hasattr(worker, "is_running"):
        return
    if not worker.is_running and worker.status not in ("completed", "paused", "error"):
        return

    prog = worker.progress

    # Sync partial plan
    if hasattr(worker, "partial_plan"):
        try:
            from Smart_Teaching_Assistance.ui.components.common import count_tree
        except ImportError:
            from components.common import count_tree
        partial = worker.partial_plan
        if partial and partial.get("topics"):
            p_count = count_tree(partial.get("topics", []))
            c_count = count_tree(st.session_state.lesson_plan.get("topics", []))
            if p_count > c_count:
                st.session_state.lesson_plan = partial

    if worker.is_running:
        st.session_state.sidebar_current_task = "Lesson Plan"
        st.session_state.sidebar_progress_frac = prog.get("frac", 0.0)
        st.session_state.sidebar_progress_label = prog.get("label", "")
        st.session_state.sidebar_eta = prog.get("eta_str", "")
        st.session_state.sidebar_elapsed = prog.get("elapsed_str", "")
        return

    if worker.status == "completed":
        lp = worker.results
        if lp:
            try:
                from Smart_Teaching_Assistance.ui.steps._shared import (
                    save_course_state, count_tree, log_activity,
                )
            except ImportError:
                from steps._shared import save_course_state, count_tree, log_activity

            st.session_state.lesson_plan = lp
            total_nodes = count_tree(
                st.session_state.get("roadmap_tree", {}).get("topics", [])
            )
            if count_tree(lp.get("topics", [])) >= total_nodes:
                st.session_state.lesson_plan_generated = True
            save_course_state()
            log_activity(
                f"Lesson plan completed: {count_tree(lp.get('topics', []))} topics",
                icon="✅", level="success",
            )

        from Smart_Teaching_Assistance.ui.state_manager import clear_sidebar_progress, set_generation_status
        set_generation_status("idle")
        clear_sidebar_progress()
        from Smart_Teaching_Assistance.ui.background_worker import BackgroundLPWorker, unregister_worker
        cid = st.session_state.get("course_id")
        if cid is not None:
            unregister_worker(cid, "lp")
        st.session_state.lp_worker = BackgroundLPWorker()
        st.session_state.pop("_global_terminal_rerun", None)
        return

    if worker.status in ("paused", "error"):
        try:
            from Smart_Teaching_Assistance.ui.steps._shared import (
                save_course_state, log_activity, log_error,
            )
        except ImportError:
            from steps._shared import save_course_state, log_activity, log_error

        partial = worker.partial_plan
        if partial and partial.get("topics"):
            st.session_state.lesson_plan = partial
            save_course_state()

        from Smart_Teaching_Assistance.ui.state_manager import clear_sidebar_progress, set_generation_status
        if worker.status == "paused":
            set_generation_status("paused_lp")
            log_activity("Lesson plan paused", icon="⏸️", level="warning")
        else:
            set_generation_status("idle")
            log_error(f"LP error: {prog.get('error', 'Unknown')}")
            from Smart_Teaching_Assistance.ui.background_worker import unregister_worker
            cid = st.session_state.get("course_id")
            if cid is not None:
                unregister_worker(cid, "lp")

        clear_sidebar_progress()
        st.session_state.pop("_global_terminal_rerun", None)


if __name__ == "__main__":
    main()
