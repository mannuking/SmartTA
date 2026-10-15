"""Step 2: Generate Roadmap — hierarchical topic tree from syllabus.

Uses BackgroundRoadmapWorker so the elapsed timer ticks in real time
(even during the AI call) and the live progress UI is light-themed
to match the rest of the app.
"""

import re
import streamlit as st
from datetime import timedelta

from Smart_Teaching_Assistance.ui.steps._shared import (
    get_config, get_db, get_content_generator, save_course_state,
    nav_buttons, log_activity, log_error, logger,
    rotate_api_key,
)
from Smart_Teaching_Assistance.ui.background_worker import (
    BackgroundRoadmapWorker, register_worker, unregister_worker,
)


_MD_ASTERISK_RE = re.compile(r"\*+")
def _strip_markdown_emph(text: str) -> str:
    """Strip * and ** markers from a user-supplied subject name."""
    return _MD_ASTERISK_RE.sub("", text or "").strip()


def _get_roadmap_worker() -> BackgroundRoadmapWorker:
    if "roadmap_worker" not in st.session_state or st.session_state.roadmap_worker is None:
        st.session_state.roadmap_worker = BackgroundRoadmapWorker()
    return st.session_state.roadmap_worker


def _sync_roadmap_worker_to_session(worker: BackgroundRoadmapWorker) -> bool:
    """Pull the worker's final result into st.session_state."""
    from Smart_Teaching_Assistance.ui.state_manager import (
        set_generation_status, clear_sidebar_progress,
    )
    if worker.status == "completed":
        result = worker.results
        if result is None:
            return False
        st.session_state.roadmap_generated = True
        st.session_state.roadmap_text = result.get("roadmap_text", "")
        st.session_state.roadmap_tree = result.get("roadmap_tree", {})
        st.session_state.roadmap_structure = result.get("roadmap_structure", {})
        st.session_state.roadmap_id = result.get("roadmap_id")
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
        set_generation_status("idle")
        clear_sidebar_progress()
        cid = st.session_state.get("course_id")
        if cid is not None:
            unregister_worker(cid, "roadmap")
        st.session_state.roadmap_worker = BackgroundRoadmapWorker()
        elapsed_str = worker.progress.get("elapsed_str", "?")
        log_activity(f"Roadmap generated in {elapsed_str}", icon="✅", level="success")
        return True
    elif worker.status == "error":
        err = worker.progress.get("error", "Unknown error")
        log_error(f"Roadmap error: {err}")
        set_generation_status("idle")
        clear_sidebar_progress()
        cid = st.session_state.get("course_id")
        if cid is not None:
            unregister_worker(cid, "roadmap")
        st.session_state.roadmap_worker = BackgroundRoadmapWorker()
        st.error(f"❌ Roadmap generation failed: {err}")
        if "timeout" in err.lower() or "deadline" in err.lower():
            st.error("Roadmap generation timed out. Please retry in a moment.")
        return False
    return False


@st.fragment(run_every=timedelta(milliseconds=500))
def _render_roadmap_live_progress(worker: BackgroundRoadmapWorker):
    import time as _time
    st.markdown('<div class="rm-live">', unsafe_allow_html=True)
    prog = worker.progress
    phase = prog.get("phase", "idle")
    message = prog.get("message", "")
    status = prog.get("status", "idle")
    t0 = getattr(worker, "_t0", 0.0) or 0.0
    elapsed_s = max(0.0, _time.time() - t0) if t0 else prog.get("elapsed", 0.0)
    if elapsed_s < 60:
        elapsed_str = f"{elapsed_s:.1f}s"
    else:
        m, s = divmod(int(elapsed_s), 60)
        elapsed_str = f"{m:02d}:{s:02d}"

    phase_icons = {
        "preparing": "🧩", "calling_ai": "🤖", "parsing": "📋",
        "storing": "💾", "done": "✅", "error": "❌",
    }
    phase_labels = {
        "preparing": "Preparing source", "calling_ai": "Calling AI",
        "parsing": "Parsing response", "storing": "Storing roadmap",
        "done": "Done", "error": "Error",
    }
    icon = phase_icons.get(phase, "🔄")
    label = phase_labels.get(phase, phase.title())
    st.markdown(
        f"""
        <div class="rm-card">
          <div class="rm-header">
            <span class="rm-pulse"></span>
            <span class="rm-title">{icon} {label}</span>
            <span class="rm-elapsed">⏱ {elapsed_str}</span>
          </div>
          <div class="rm-message">{message or "&nbsp;"}</div>
          <div class="rm-bar-track"><div class="rm-bar-fill"></div></div>
          <div class="rm-status">{status.title()}</div>
        </div>
        <style>
          .rm-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-left: 4px solid var(--primary);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            margin: 0.5rem 0 1rem 0;
            box-shadow: var(--shadow-sm);
            color: var(--text);
          }}
          .rm-header {{
            display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.4rem;
          }}
          .rm-pulse {{
            width: 12px; height: 12px; border-radius: 50%;
            background: var(--primary);
            box-shadow: 0 0 0 0 rgba(79, 70, 229, 0.5);
            animation: rm-pulse 1.5s infinite;
          }}
          .rm-title {{
            color: var(--text); font-weight: 600; font-size: 1.0rem;
          }}
          .rm-elapsed {{
            margin-left: auto; color: var(--text-secondary);
            font-family: 'JetBrains Mono', 'Consolas', monospace;
            font-size: 0.95rem; font-weight: 500;
          }}
          .rm-message {{
            color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 0.6rem;
          }}
          .rm-bar-track {{
            background: var(--border); border-radius: 6px; height: 6px; overflow: hidden;
          }}
          .rm-bar-fill {{
            background: linear-gradient(90deg, var(--primary), var(--accent));
            height: 100%; width: 100%;
            background-size: 200% 100%;
            animation: rm-shimmer 1.5s linear infinite;
          }}
          .rm-status {{
            color: var(--text-muted); font-size: 0.8rem; font-weight: 500;
            margin-top: 0.5rem; text-transform: uppercase; letter-spacing: 0.05em;
          }}
          @keyframes rm-pulse {{
            0%   {{ box-shadow: 0 0 0 0 rgba(79, 70, 229, 0.5); }}
            70%  {{ box-shadow: 0 0 0 10px rgba(79, 70, 229, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(79, 70, 229, 0); }}
          }}
          @keyframes rm-shimmer {{
            0%   {{ background-position: 200% 0; }}
            100% {{ background-position: -200% 0; }}
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('</div>', unsafe_allow_html=True)
    if status in ("completed", "error"):
        if not st.session_state.get("_rm_terminal_rerun"):
            st.session_state._rm_terminal_rerun = True
            st.rerun(scope="app")


def _build_roadmap_callable(phase_cb, source: str, difficulty: str):
    def _do_it():
        config = get_config()
        db = get_db(config)
        ContentGenerator = get_content_generator()
        gen = ContentGenerator(
            config.gemini_api_key, db,
            model_name=config.gemini_model,
            rotation_callback=rotate_api_key,
            user_id=st.session_state.user_id,
        )
        phase_cb("calling_ai", f"model={config.gemini_model or 'gemini-flash'}, source={len(source):,} chars")
        result = gen.generate_and_store_roadmap(
            source, difficulty, subject=st.session_state.subject_name
        )
        phase_cb("parsing", f"{len(result.get('roadmap_text',''))} chars returned")
        return result
    return _do_it


def step_roadmap():
    if not st.session_state.syllabus_text:
        st.warning("⚠️ Provide a subject and syllabus first.")
        nav_buttons(next_=False, back_step=0)
        return

    # Clean subject name (strip any ** or * markers the user typed)
    clean_subject = _strip_markdown_emph(st.session_state.subject_name)
    st.markdown(
        f"Generate a **hierarchical roadmap (tree structure)** for **{clean_subject}**."
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

    worker = _get_roadmap_worker()

    if worker.status in ("completed", "error"):
        if _sync_roadmap_worker_to_session(worker):
            st.session_state.pop("_rm_terminal_rerun", None)
            st.rerun()
        worker = _get_roadmap_worker()

    if worker.is_running or worker.status in ("running", "completed", "error"):
        _render_roadmap_live_progress(worker)
        st.button(
            "⏳ Generating...",
            type="secondary",
            disabled=True,
            use_container_width=True,
        )
    else:
        if st.button(
            btn_label,
            type="primary" if not st.session_state.roadmap_generated else "secondary",
            use_container_width=True,
        ):
            source_parts: list = [st.session_state.syllabus_text]
            if st.session_state.get("course_corpus") is not None:
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
            phase_cb = worker.update_phase
            phase_cb("preparing", "resolving RAG excerpts from the uploaded PDF")
            callable_ = _build_roadmap_callable(phase_cb, source, difficulty)
            started = worker.start(gen_callable=callable_, gen_kwargs={})
            if started:
                from Smart_Teaching_Assistance.ui.state_manager import (
                    set_generation_status, update_sidebar_progress,
                )
                set_generation_status("generating_rm")
                cid = st.session_state.get("course_id")
                if cid is not None:
                    register_worker(cid, "roadmap", worker)
                log_activity("Generating roadmap...", icon="🗺️", level="progress")
                update_sidebar_progress(task="Roadmap")
                st.session_state.pop("_rm_terminal_rerun", None)
                st.rerun()

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
