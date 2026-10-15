"""Step 4: Detailed Notes — rich lecture notes with pedagogical elements + study tools."""

import streamlit as st
import os
import time as _time
from datetime import timedelta

from Smart_Teaching_Assistance.ui.steps._shared import (
    get_config, get_db, get_content_generator, save_course_state,
    nav_buttons, count_tree, render_note_elements,
    ProgressTracker, make_pause_callback, fmt_duration,
    log_activity, log_error, logger, PauseRequested,
    topic_heading_level, clean_topic_title, topic_display_label,
    rotate_api_key,
)
from Smart_Teaching_Assistance.ui.background_worker import BackgroundNotesWorker, register_worker, unregister_worker


# ── Helper: get or init the background worker ───────────────────────────────

def _get_worker() -> BackgroundNotesWorker:
    """Return the background notes worker, creating one if needed."""
    if "notes_worker" not in st.session_state or st.session_state.notes_worker is None:
        st.session_state.notes_worker = BackgroundNotesWorker()
    return st.session_state.notes_worker


# ── Main step function ───────────────────────────────────────────────────────

def step_detailed_notes():
    if not st.session_state.lesson_plan_generated:
        st.warning("⚠️ Generate a lesson plan first.")
        nav_buttons(next_=False, back_step=2)
        return

    save_course_state()
    st.markdown("Generate rich **lecture notes** with pedagogical elements.")

    total_needed = count_tree(st.session_state.lesson_plan.get("topics", []))
    worker = _get_worker()

    # ── Sync partial results from background worker ──────────────────
    _sync_worker_to_session(worker, total_needed)
    # Re-read after sync (sync may harvest final results or reset the worker)
    completed = len(st.session_state.all_notes_data)
    worker = _get_worker()

    # ── Detect interrupted generation (e.g. browser refresh killed worker) ──
    gen_status = st.session_state.get("generation_status", "idle")
    is_interrupted = (
        gen_status in ("generating_notes", "paused_notes")
        and not worker.is_running
        and worker.status == "idle"
    )

    # "Done" means the worker reported completion OR we already had the
    # flag set from a previous run. Previously this checked
    # `len(all_notes_data) >= total_needed` which silently failed when
    # the worker's `completed` count was slightly off (e.g. 71/72
    # because one sub-topic was rolled into a parent), so the UI kept
    # showing "Partial: 71/72 notes generated" forever.
    notes_done = (
        st.session_state.detailed_notes_generated
        or worker.status == "completed"
    ) and not worker.is_running

    if notes_done:
        # ── Notes completed — show image management section ──────────
        st.success(f"✅ Notes complete! ({completed} topics)")
        _render_image_section()
        _render_preview_section(total_needed)
    else:
        # ── Generation controls ──────────────────────────────────────
        if is_interrupted:
            if completed > 0:
                st.warning(
                    f"⚠️ Notes generation was **interrupted** — "
                    f"**{completed} / {total_needed}** notes saved. "
                    "Click **Generate/Resume** to continue from where you left off."
                )
            else:
                st.warning(
                    "⚠️ Notes generation was **interrupted** before any topics completed. "
                    "Click **Generate/Resume** to restart."
                )
        elif 0 < completed < total_needed and not worker.is_running:
            st.warning(f"⏩ Partial: **{completed} / {total_needed}** notes generated.")

        highlights = st.text_area(
            "✍️ Extra emphasis on (IDs like T1.1):",
            value=st.session_state.highlighted_topics_input,
            height=70,
        )
        st.session_state.highlighted_topics_input = highlights
        llm_temp = st.slider("🌡️ AI Creativity", 0.1, 1.0, 0.7, step=0.05, key="temp_notes")

        # ── Auto-resume from dashboard ───────────────────────────────
        auto_resume = st.session_state.pop("_auto_resume", None)

        # ── Running / Paused: show live progress ─────────────────────
        if worker.is_running or worker.status == "pausing":
            _render_live_progress(worker, total_needed)
        elif worker.status == "paused":
            st.info(
                f"⏸️ Generation paused — **{len(st.session_state.all_notes_data)} / {total_needed}** "
                "notes saved. Click **Generate/Resume** to continue."
            )
            if auto_resume == "notes" or st.button("🚀 Generate/Resume Notes", type="primary"):
                _start_background_generation(total_needed, highlights, llm_temp)
        elif worker.status == "error":
            prog = worker.progress
            st.error(f"❌ Generation error: {prog.get('error', 'Unknown error')}")
            if auto_resume == "notes" or st.button("🚀 Retry Generation", type="primary"):
                _start_background_generation(total_needed, highlights, llm_temp)
        else:
            # idle or completed — show Generate button
            if auto_resume == "notes" or st.button("🚀 Generate/Resume Notes", type="primary"):
                _start_background_generation(total_needed, highlights, llm_temp)

        # ── Preview (only when worker is not actively running) ───────
        if not (worker.is_running or worker.status == "pausing"):
            _render_preview_section(total_needed)

    # ── Bottom Navigation ────────────────────────────────────────────────
    st.markdown("---")
    c_back, c_skip, c_next = st.columns([1, 1, 1])
    with c_back:
        if st.button("⬅️ Back", use_container_width=True):
            st.session_state.step = 2
            st.rerun()
    with c_skip:
        if st.button("⏩ Skip to Export", use_container_width=True):
            st.session_state.step = 5
            st.rerun()
    with c_next:
        next_disabled = not st.session_state.detailed_notes_generated
        if st.button(
            "Personalize (Optional) ➡️",
            type="primary",
            use_container_width=True,
            disabled=next_disabled,
        ):
            st.session_state.step = 4
            st.rerun()


# ── Private: Start background generation ─────────────────────────────────────

def _start_background_generation(total_needed: int, highlights: str, llm_temp: float):
    """Kick off note generation in a background thread."""
    worker = _get_worker()

    # Guard: never start a second worker if one is already running
    if worker.is_running:
        logger.warning("Ignoring duplicate generation request — worker already running")
        return

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
        st.session_state.notes_worker = BackgroundNotesWorker()
        worker = st.session_state.notes_worker

    highlight_list = [h.strip() for h in highlights.split(",") if h.strip()]

    gen_kwargs = dict(
        lesson_plan=st.session_state.lesson_plan,
        subject=st.session_state.subject_name,
        difficulty=st.session_state.difficulty,
        highlights=highlight_list,
        temperature=llm_temp,
        existing_notes=st.session_state.all_notes_data,
        roadmap_tree=st.session_state.roadmap_tree,
        partial_update_cb=worker.update_partial,
    )

    # If the user uploaded a PDF and we have a RAG corpus, thread a
    # book_context callback through so each topic's notes generation
    # gets a RAG-retrieved string of book excerpts + figure captions.
    if st.session_state.get("course_corpus") is not None:
        from Smart_Teaching_Assistance.ui.rag_bridge import get_rag_context
        # Capture by value (not reference) -- the lambda must be a
        # top-level closure on a snapshot, not a ref to live session.
        _has_rag = True
        def _book_context_for(topic_id: str, topic_title: str) -> str:
            if not _has_rag:
                return ""
            try:
                rag_ctx = get_rag_context(
                    topic_title or topic_id,
                    section_id=topic_id,
                    k_text=5, k_figures=2, rerank=True,
                )
                return rag_ctx.as_prompt_block()
            except Exception as e:
                logger.debug(f"RAG lookup for {topic_id} failed: {e}")
                return ""
        gen_kwargs["book_context_fn"] = _book_context_for

    # Clear guards for the new generation
    st.session_state.pop("_notes_terminal_rerun", None)
    st.session_state.pop("_global_terminal_rerun", None)

    started = worker.start(
        gen_callable=gen.generate_detailed_notes_recursive,
        gen_kwargs=gen_kwargs,
        existing_notes=st.session_state.all_notes_data,
    )

    if started:
        from Smart_Teaching_Assistance.ui.state_manager import set_generation_status, update_sidebar_progress
        set_generation_status("generating_notes")
        # Register in process-level registry so browser refresh can reconnect
        cid = st.session_state.get("course_id")
        if cid is not None:
            register_worker(cid, "notes", worker)
        log_activity("Notes generation started (background)", icon="🚀", level="progress")
        update_sidebar_progress(task="Detailed Notes")
    st.rerun()


# ── Private: Sync worker state → session state ──────────────────────────────

def _sync_worker_to_session(worker: BackgroundNotesWorker, total_needed: int):
    """Pull partial results + progress from the background worker into session."""
    from Smart_Teaching_Assistance.ui.state_manager import (
        update_sidebar_progress, clear_sidebar_progress, set_generation_status,
    )

    if worker.status == "idle":
        return

    prog = worker.progress

    # Always sync partial notes so user sees progress
    partial = worker.partial_notes
    if partial and len(partial) > len(st.session_state.all_notes_data):
        st.session_state.all_notes_data = partial
        # Auto-save periodically
        if len(partial) % 3 == 0:
            save_course_state()

    if worker.status == "running" or worker.status == "pausing":
        update_sidebar_progress(
            task="Detailed Notes",
            frac=prog.get("frac", 0.0),
            label=prog.get("label", ""),
            eta=prog.get("eta_str", ""),
            elapsed=prog.get("elapsed_str", ""),
        )

    elif worker.status == "completed":
        # Guard: only harvest once (the sync resets the worker to a new instance)
        if worker.results is None:
            return
        # Harvest final results
        results = worker.results
        if results:
            all_notes, answer_key = results
            ContentGenerator = get_content_generator()
            st.session_state.all_notes_data = all_notes
            st.session_state.answer_key_text = answer_key
            st.session_state.notes_markdown = ContentGenerator.elements_to_markdown(
                all_notes, answer_key
            )
            st.session_state.glossary = ContentGenerator.extract_glossary(all_notes)
            # Mark "done" whenever the worker reports completion -- even if
            # `len(all_notes) < total_needed` because some sub-topics were
            # rolled into parents by the generator. The user has every
            # note we have; this just avoids the "Partial: N/M" trap.
            st.session_state.detailed_notes_generated = True

            # If the user uploaded a PDF, auto-place the best-matching
            # extracted figure at each sub-section (via CLIP cosine sim).
            # Mutates `all_notes` in place to add `figure_block` elements.
            if st.session_state.get("course_corpus") is not None:
                try:
                    from Smart_Teaching_Assistance.ui.rag_bridge import place_figures_into_notes
                    all_notes = place_figures_into_notes(all_notes)
                    st.session_state.all_notes_data = all_notes
                    # Also rebuild the markdown so the figure captions
                    # appear in the on-screen view immediately.
                    st.session_state.notes_markdown = ContentGenerator.elements_to_markdown(
                        all_notes, answer_key
                    )
                    placed = sum(
                        1 for n in all_notes for e in n.get("elements", [])
                        if e.get("type") == "figure_block"
                    )
                    if placed:
                        log_activity(
                            f"Placed {placed} figure(s) from the uploaded PDF into the notes.",
                            icon="🖼️", level="success",
                        )
                except Exception as e:
                    logger.warning(f"figure placement failed: {e}")

            # Persist notes to the per-user, per-course folder on
            # disk so the user can re-open Step 4 (or jump to
            # Step 6) after a server restart without re-running
            # the entire pipeline.
            try:
                from Smart_Teaching_Assistance.ui.rag_bridge import save_notes_for_course
                _pdf_hash = st.session_state.get("pdf_hash") or (
                    st.session_state.course_corpus.pdf_hash
                    if st.session_state.get("course_corpus") else ""
                )
                if _pdf_hash:
                    save_notes_for_course(
                        _pdf_hash,
                        all_notes,
                        answer_key_text=answer_key,
                        notes_markdown=st.session_state.get("notes_markdown", ""),
                    )
            except Exception as e:
                logger.warning(f"notes persist failed: {e}")

            save_course_state()
            set_generation_status("idle")

            # Image stats for log
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

        clear_sidebar_progress()
        # Unregister from process-level registry and reset worker to idle
        cid = st.session_state.get("course_id")
        if cid is not None:
            unregister_worker(cid, "notes")
        st.session_state.notes_worker = BackgroundNotesWorker()

    elif worker.status == "paused":
        partial = worker.partial_notes
        if partial:
            st.session_state.all_notes_data = partial
            save_course_state()
        set_generation_status("paused_notes")
        log_activity(
            f"Notes paused at {len(st.session_state.all_notes_data)}/{total_needed}",
            icon="⏸️", level="warning",
        )
        clear_sidebar_progress()

    elif worker.status == "error":
        err_msg = prog.get("error", "Unknown error")
        log_error(f"Notes generation error: {err_msg}")
        # Still save any partial notes
        partial = worker.partial_notes
        if partial:
            st.session_state.all_notes_data = partial
            save_course_state()
        set_generation_status("idle")
        clear_sidebar_progress()
        cid = st.session_state.get("course_id")
        if cid is not None:
            unregister_worker(cid, "notes")


# ── Private: Live progress display (auto-refreshing fragment) ────────────────

# Rerun twice per second so elapsed/ETA/progress tick visibly instead of
# freezing on int boundaries.
@st.fragment(run_every=timedelta(milliseconds=500))
def _render_live_progress(worker: BackgroundNotesWorker, total_needed: int):
    """Auto-refreshing fragment showing live progress + partial notes preview.

    Everything lives INSIDE the fragment so it auto-updates every 2 s
    without needing st.rerun(scope='app'), which caused duplicate workers.
    """
    prog = worker.progress
    status = prog.get("status", "idle")
    phase = prog.get("phase", "notes")
    completed = prog.get("completed", 0)
    total = prog.get("total", total_needed)
    frac = prog.get("frac", 0.0)
    message = prog.get("message", "")
    elapsed_str = prog.get("elapsed_str", "")
    eta_str = prog.get("eta_str", "")

    # Sync partial notes on each refresh (no full-app rerun)
    _sync_worker_to_session(worker, total_needed)

    # ── Image-fetching phase: different header & metrics ─────────────
    if phase == "images":
        st.markdown("### 🖼️ Fetching Images...")
        st.progress(1.0)
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("⏱️ Elapsed", elapsed_str or "0s")
        mc2.metric("📊 Notes", f"✅ {completed} / {total}")
        mc3.metric("📝 Phase", "Downloading Images")
        if message:
            st.markdown(f"🔄 **{message}**")
    else:
        st.markdown("### 📖 Generating Notes...")
        st.progress(frac)
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("⏱️ Elapsed", elapsed_str or "0s")
        mc2.metric("📊 Progress", f"{completed} / {total}")
        mc3.metric("⏳ ETA", eta_str or "Calculating...")
        mc4.metric("📝 Status", "Running" if status == "running" else status.title())
        if message:
            st.markdown(f"🔄 **{message}** · {frac*100:.0f}%")

    # Pause button (only during notes phase, not image fetch)
    if phase != "images":
        if status == "running":
            if st.button("⏸️ Pause Generation", type="secondary"):
                worker.pause()
        elif status == "pausing":
            st.info("⏳ Pausing after current topic...")

    # ── Inline partial preview (inside fragment, auto-updates) ───────
    partial = st.session_state.get("all_notes_data", [])
    if partial:
        col1, col2 = st.columns([1, 2.5])
        with col1:
            with st.container(height=400):
                for n in partial:
                    is_active = n["id"] == st.session_state.get("active_note_id", partial[0]["id"])
                    if st.button(
                        f"{n['id']}: {n['title']}",
                        key=f"live_{n['id']}",
                        type="primary" if is_active else "secondary",
                        use_container_width=True,
                    ):
                        st.session_state.active_note_id = n["id"]
        with col2:
            active_id = st.session_state.get("active_note_id", partial[0]["id"])
            note = next((n for n in partial if n["id"] == active_id), partial[-1])
            hlevel = topic_heading_level(note["id"])
            st.markdown(f"{'#' * hlevel} {topic_display_label(note)}")
            render_note_elements(note, heading_offset=note["id"].count('.'))

    # If worker finished between refreshes, trigger ONE full-app rerun
    if status in ("completed", "paused", "error"):
        if not st.session_state.get("_notes_terminal_rerun"):
            st.session_state._notes_terminal_rerun = True
            st.rerun(scope="app")


# ── Private: Review & Polish ─────────────────────────────────────────────────

def _render_review_section():
    st.markdown("**Post-Generation Review**")
    st.caption("Normalizes section lengths, removes redundancy between consecutive sections")
    review_status = "✅ Completed" if st.session_state.review_completed else "⏳ Not yet run"
    st.info(f"Status: {review_status}")

    if st.button(
        "🔬 Review & Polish Notes",
        type="primary" if not st.session_state.review_completed else "secondary",
    ):
        with st.status("🔬 Reviewing notes quality...", expanded=True) as review_s:
            try:
                config = get_config()
                db = get_db(config)
                ContentGenerator = get_content_generator()
                gen = ContentGenerator(
                    config.gemini_api_key, 
                    db, 
                    model_name=config.gemini_model,
                    rotation_callback=rotate_api_key
                )

                tracker = ProgressTracker("Review & Polish", parent_status=review_s)

                reviewed = gen.review_and_normalize(
                    st.session_state.all_notes_data,
                    st.session_state.subject_name,
                    progress_cb=tracker.on_progress,
                )

                tracker.finalize(len(reviewed), len(reviewed))

                st.session_state.all_notes_data = reviewed
                st.session_state.notes_markdown = ContentGenerator.elements_to_markdown(
                    reviewed, st.session_state.answer_key_text
                )
                st.session_state.glossary = ContentGenerator.extract_glossary(reviewed)
                st.session_state.review_completed = True
                save_course_state()
                st.rerun()
            except Exception as e:
                st.error(f"Review error: {e}")
                log_error(f"Review error: {e}")

    # Chapter Summaries
    if st.button(
        "📋 Generate Chapter Summaries",
        disabled=bool(st.session_state.chapter_summaries),
    ):
        with st.status("📋 Generating summaries...", expanded=True) as sum_s:
            try:
                config = get_config()
                db = get_db(config)
                ContentGenerator = get_content_generator()
                summaries = gen.generate_chapter_summaries(
                    st.session_state.all_notes_data,
                    st.session_state.subject_name,
                    st.session_state.roadmap_tree,
                    progress_cb=tracker.on_progress,
                )

                tracker.finalize(len(summaries), len(summaries))

                st.session_state.chapter_summaries = summaries
                save_course_state()
                st.rerun()
            except Exception as e:
                st.error(f"Summary error: {e}")
                log_error(f"Summary error: {e}")

    if st.session_state.chapter_summaries:
        st.success(f"✅ {len(st.session_state.chapter_summaries)} chapter summaries ready")


# ── Private: Exam Prep Tools ────────────────────────────────────────────────

def _render_exam_tools():
    st.markdown("**Exam Preparation Tools**")
    ContentGenerator = get_content_generator()

    # ── Flashcards ───────────────────────────────────────────────────────
    fc_count = len(st.session_state.flashcards)
    if st.button(
        f"🃏 Generate Flashcards" + (f" ({fc_count} ready)" if fc_count else ""),
        type="primary" if not fc_count else "secondary",
    ):
        with st.status("🃏 Creating flashcards...", expanded=True) as fc_s:
            try:
                config = get_config()
                db = get_db(config)
                gen = ContentGenerator(
                    config.gemini_api_key, 
                    db, 
                    model_name=config.gemini_model,
                    rotation_callback=rotate_api_key
                )

                tracker = ProgressTracker("Flashcards", parent_status=fc_s)

                cards = gen.generate_flashcards(
                    st.session_state.all_notes_data,
                    st.session_state.subject_name,
                    progress_cb=tracker.on_progress,
                )

                tracker.finalize(len(cards), len(cards), extra_metric=("🃏 Created", f"{len(cards)} cards"))

                st.session_state.flashcards = cards
                save_course_state()
                st.rerun()
            except Exception as e:
                st.error(f"Flashcard error: {e}")
                log_error(f"Flashcard error: {e}")

    # ── Question Bank ────────────────────────────────────────────────────
    qb_count = len(st.session_state.question_bank)
    if st.button(
        f"❓ Generate Question Bank" + (f" ({qb_count} ready)" if qb_count else ""),
        type="primary" if not qb_count else "secondary",
    ):
        with st.status("❓ Building question bank...", expanded=True) as qb_s:
            try:
                config = get_config()
                db = get_db(config)
                gen = ContentGenerator(
                    config.gemini_api_key, 
                    db, 
                    model_name=config.gemini_model,
                    rotation_callback=rotate_api_key
                )

                tracker = ProgressTracker("Question Bank", parent_status=qb_s)

                questions = gen.generate_question_bank(
                    st.session_state.all_notes_data,
                    st.session_state.subject_name,
                    st.session_state.difficulty,
                    progress_cb=tracker.on_progress,
                )

                tracker.finalize(
                    len(questions), len(questions),
                    extra_metric=("❓ Created", f"{len(questions)} questions"),
                )

                st.session_state.question_bank = questions
                save_course_state()
                st.rerun()
            except Exception as e:
                st.error(f"Question bank error: {e}")
                log_error(f"Question bank error: {e}")


# ── Private: Image management after notes are done ───────────────────────────

def _get_image_stats():
    """Scan all_notes_data and return (total_placeholders, found, missing_elements).

    Counts BOTH image_placeholder (LLM-requested web images) AND
    figure_block (locally-extracted PDF figures). The previous
    version only counted image_placeholder, which made it look like
    0 figures were placed even when figure_placer had successfully
    injected 10 figure_blocks from the uploaded PDF.
    """
    all_notes = st.session_state.get("all_notes_data", [])
    total = 0
    found = 0
    missing = []
    for topic in all_notes:
        for el in topic.get("elements", []):
            etype = el.get("type", "")
            if etype == "image_placeholder":
                # LLM-requested web image; we check image_local_path.
                total += 1
                local = el.get("image_local_path", "")
                if local and os.path.isfile(local):
                    found += 1
                else:
                    missing.append(el)
            elif etype == "figure_block":
                # Locally-extracted PDF figure; we check image_path.
                total += 1
                local = el.get("image_path", "")
                if local and os.path.isfile(local):
                    found += 1
                else:
                    # Only mark missing if the file truly doesn't exist
                    # (not just unindexed). Caption is still useful
                    # context for the user.
                    missing.append(el)
    return total, found, missing


def _render_image_section():
    """Show image fetch status and retry controls after notes are complete."""
    # ── Auto-restore banner (survives a server restart) ─────────────────
    # If pdf_hash is in session_state but the in-memory course_corpus
    # is gone (typical after a server restart), the 10 figures are
    # still on disk in figures_index.json. Show a one-click rehydrate
    # so the user doesn't have to navigate back to Step 1.
    pdf_hash = st.session_state.get("pdf_hash")
    if pdf_hash and st.session_state.get("course_corpus") is None:
        try:
            from Smart_Teaching_Assistance.ui.rag_bridge import (
                load_corpus_for_session,
                load_figures_for_course,
                user_courses_base,
            )
            figs = load_figures_for_course(pdf_hash)
            if figs:
                with st.container(border=True):
                    cols = st.columns([4, 2])
                    cols[0].markdown(
                        f"💾 **{len(figs)} figure(s)** saved on disk from your PDF, "
                        f"but the in-memory corpus is empty (probably after a server "
                        f"restart). Click to re-load them — no need to re-upload."
                    )
                    if cols[1].button(
                        f"🔄 Re-load {len(figs)} figure(s) from disk",
                        type="primary",
                        use_container_width=True,
                        key="reload_figures_from_disk",
                    ):
                        courses_dir = user_courses_base()
                        corpus = load_corpus_for_session(pdf_hash)
                        if corpus:
                            st.session_state.course_corpus = corpus
                            st.session_state.courses_dir = str(courses_dir)
                            st.session_state.rag_ready = False
                            st.success(
                                f"Re-loaded {len(corpus.figures)} figure(s) from disk."
                            )
                            log_activity(
                                f"Re-loaded {len(corpus.figures)} figure(s) from disk.",
                                icon="💾", level="success",
                            )
                            st.rerun()
                        else:
                            st.error(
                                f"No corpus on disk for pdf_hash={pdf_hash[:8]}. "
                                f"You'll need to re-upload the PDF in Step 1."
                            )
        except Exception as restore_err:
            logger.debug(f"auto-restore banner skipped: {restore_err}")

    total_imgs, found_imgs, missing_els = _get_image_stats()

    if total_imgs == 0:
        # No image_placeholder elements yet -- but maybe the user uploaded
        # a PDF and we have figure_block candidates that haven't been
        # placed.  Show a one-click attach button in that case.
        corpus = st.session_state.get("course_corpus")
        n_source = len(corpus.figures) if corpus and hasattr(corpus, 'figures') else 0
        st.markdown("### 🖼️ Image Status")
        if n_source:
            st.info(
                f"📦 **{n_source} figure(s)** are available from your uploaded PDF "
                f"but none are placed in the notes yet. "
                f"Click below to attach them to the matching topics."
            )
            if st.button(
                f"🖼️ Attach {n_source} PDF figure(s) to the notes",
                type="primary",
                use_container_width=True,
            ):
                try:
                    from Smart_Teaching_Assistance.ui.rag_bridge import place_figures_into_notes
                    st.session_state.all_notes_data = place_figures_into_notes(
                        st.session_state.all_notes_data
                    )
                    save_course_state()
                    placed = sum(
                        1 for n in st.session_state.all_notes_data
                        for e in n.get("elements", [])
                        if e.get("type") == "figure_block"
                    )
                    log_activity(
                        f"Attached {placed} PDF figure(s) to the notes.",
                        icon="🖼️", level="success",
                    )
                    st.toast(f"Placed {placed} figure(s) — re-render to see them.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Figure attach failed: {e}")
                    logger.error(f"figure attach failed: {e}", exc_info=True)
        else:
            st.info("ℹ️ No image placeholders in the generated notes.")
        return

    st.markdown("### 🖼️ Image Status")

    # Count separately: image_placeholder (LLM web images) vs figure_block (PDF figures)
    n_placeholders = 0
    n_placeholder_found = 0
    n_placeholder_missing = 0
    n_figure_blocks = 0
    n_figure_blocks_ok = 0
    for topic in st.session_state.get("all_notes_data", []):
        for el in topic.get("elements", []):
            etype = el.get("type", "")
            if etype == "image_placeholder":
                n_placeholders += 1
                local = el.get("image_local_path", "")
                if local and os.path.isfile(local):
                    n_placeholder_found += 1
                else:
                    n_placeholder_missing += 1
            elif etype == "figure_block":
                n_figure_blocks += 1
                local = el.get("image_path", "")
                if local and os.path.isfile(local):
                    n_figure_blocks_ok += 1

    # Show the two pools as separate sub-tables
    if n_placeholders:
        col_ok, col_miss, col_total = st.columns(3)
        col_ok.metric("✅ Web Imgs", n_placeholder_found)
        col_miss.metric("❌ Web Missing", n_placeholder_missing)
        col_total.metric("📊 Web Total", n_placeholders)
    if n_figure_blocks:
        col_ok2, col_miss2, col_total2 = st.columns(3)
        col_ok2.metric("✅ PDF Figs", n_figure_blocks_ok)
        miss_fb = n_figure_blocks - n_figure_blocks_ok
        col_miss2.metric("❌ PDF Missing", miss_fb)
        col_total2.metric("📊 PDF Total", n_figure_blocks)

    # Reuse the existing total/found for the legacy retry button path
    total_imgs = n_placeholders
    found_imgs = n_placeholder_found
    missing_els = [el for topic in st.session_state.get("all_notes_data", [])
                   for el in topic.get("elements", [])
                   if el.get("type") == "image_placeholder"
                   and not (el.get("image_local_path", "") and os.path.isfile(el.get("image_local_path", "")))]

    if not n_placeholders and not n_figure_blocks:
        return

    if not missing_els:
        st.success("All web images fetched successfully!")
        return

    if n_placeholders:
        st.warning(f"**{n_placeholder_missing}** web image(s) could not be fetched. You can retry or provide URLs manually.")

    # ── Re-attach PDF figures button ──────────────────────────────────
    # If the user uploaded a PDF and figures haven't been placed in the
    # notes yet, show a one-click attach button right here.
    corpus = st.session_state.get("course_corpus")
    n_source = len(corpus.figures) if corpus and hasattr(corpus, 'figures') else 0
    if n_source and n_figure_blocks < n_source:
        st.warning(
            f"📦 **{n_source} figure(s)** extracted from your uploaded PDF but only "
            f"**{n_figure_blocks}** are placed in the notes. Click below to attach the rest."
        )
        if st.button(
            f"🖼️ Attach {n_source - n_figure_blocks} remaining PDF figure(s)",
            type="primary",
            use_container_width=True,
        ):
            try:
                from Smart_Teaching_Assistance.ui.rag_bridge import place_figures_into_notes
                st.session_state.all_notes_data = place_figures_into_notes(
                    st.session_state.all_notes_data
                )
                save_course_state()
                # Also rebuild notes_markdown so the captions appear in the on-screen view
                try:
                    ContentGenerator = get_content_generator()
                    st.session_state.notes_markdown = ContentGenerator.elements_to_markdown(
                        st.session_state.all_notes_data,
                        st.session_state.get("answer_key_text", ""),
                    )
                except Exception:
                    pass
                placed = sum(
                    1 for n in st.session_state.all_notes_data
                    for e in n.get("elements", [])
                    if e.get("type") == "figure_block"
                )
                log_activity(
                    f"Attached {placed} PDF figure(s) to the notes.",
                    icon="🖼️", level="success",
                )
                st.toast(f"Now {placed} PDF figure(s) placed in the notes.")
                st.rerun()
            except Exception as e:
                st.error(f"Figure attach failed: {e}")
                logger.error(f"figure attach failed: {e}", exc_info=True)

    # ── Retry button ─────────────────────────────────────────────────
    if st.button("🔄 Retry Failed Images", type="primary"):
        from Smart_Teaching_Assistance.content_generator.image_downloader import populate_images_parallel

        with st.status(f"🖼️ Retrying {len(missing_els)} images...", expanded=True) as img_status:
            def _retry_progress(done, total, msg):
                img_status.update(label=f"🖼️ {msg}")

            populate_images_parallel(
                st.session_state.all_notes_data,
                progress_cb=_retry_progress,
            )
            save_course_state()
        st.rerun()

    # ── Manual URL insertion ─────────────────────────────────────────
    with st.expander("📎 Manually set image URLs", expanded=False):
        st.caption("Paste a direct image URL for any missing placeholder. Leave blank to skip.")
        changed = False
        for i, el in enumerate(missing_els):
            query = el.get("search_query", "unknown")
            caption = el.get("caption", "")
            label = f"**{caption or query}**"
            url = st.text_input(label, key=f"manual_img_{i}", placeholder="https://...")
            if url and url.startswith("https://"):
                el["image_url"] = url
                el["image_local_path"] = ""  # will display from URL
                changed = True
        if changed:
            if st.button("💾 Save Manual URLs"):
                save_course_state()
                st.rerun()


# ── Private: Preview Section ─────────────────────────────────────────────────

def _render_preview_section(total_needed: int):
    if st.session_state.all_notes_data:
        _render_notes_tab("prev_")


def _render_notes_tab(key_prefix: str):
    """Shared notes preview with navigation tree."""
    col1, col2 = st.columns([1, 2.5])
    with col1:
        with st.container(height=500):
            if not st.session_state.active_note_id:
                st.session_state.active_note_id = st.session_state.all_notes_data[0]["id"]
            for n in st.session_state.all_notes_data:
                is_active = n["id"] == st.session_state.active_note_id
                if st.button(
                    f"{n['id']}: {n['title']}",
                    key=f"{key_prefix}{n['id']}",
                    type="primary" if is_active else "secondary",
                    use_container_width=True,
                ):
                    st.session_state.active_note_id = n["id"]
                    st.rerun()
    with col2:
        note = next(
            (n for n in st.session_state.all_notes_data if n["id"] == st.session_state.active_note_id),
            None,
        )
        if note:
            hlevel = topic_heading_level(note["id"])
            st.markdown(f"{'#' * hlevel} {topic_display_label(note)}")
            render_note_elements(note, heading_offset=note["id"].count('.'))



