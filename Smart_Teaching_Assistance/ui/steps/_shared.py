"""Shared imports, helpers, and utilities used across all step modules."""

import streamlit as st
import os
import json
import tempfile
import time
import logging
import importlib
from pathlib import Path
import sys

# ── Ensure project root on sys.path ──────────────────────────────────────────
current_file = Path(__file__).resolve()
root_dir = str(current_file.parents[3])  # steps → ui → STA → project root
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# ── Core imports ─────────────────────────────────────────────────────────────
from Smart_Teaching_Assistance.ui.state_manager import (
    get_config, get_db, save_course_state, MAX_BOOK_TOKENS, go,
    log_activity, update_sidebar_progress, clear_sidebar_progress, log_error,
    rotate_api_key,
)
from Smart_Teaching_Assistance.ui.components.common import nav_buttons, count_tree
from Smart_Teaching_Assistance import format_error_for_ui, PauseRequested

logger = logging.getLogger(__name__)


# ── Lazy-loaded heavy imports (avoid circular / slow startup) ────────────────
def get_content_generator():
    """Return the ContentGenerator class, force-reloading for live dev."""
    for _mod in (
        'Smart_Teaching_Assistance.content_generator.lecture_notes',
        'Smart_Teaching_Assistance.content_generator.generator',
        'Smart_Teaching_Assistance.content_generator',
    ):
        if _mod in sys.modules:
            importlib.reload(sys.modules[_mod])
    from Smart_Teaching_Assistance.content_generator import ContentGenerator
    return ContentGenerator


def get_export_manager():
    """Return the ExportManager class, force-reloading for live dev."""
    _mod = 'Smart_Teaching_Assistance.export_manager.exporter'
    if _mod in sys.modules:
        importlib.reload(sys.modules[_mod])
    from Smart_Teaching_Assistance.export_manager.exporter import ExportManager
    return ExportManager


# ── Shared duration formatter ────────────────────────────────────────────────
def fmt_duration(secs: float) -> str:
    """Format seconds → human-readable string like '2m 34s'."""
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


# ── Reusable progress-callback factory ───────────────────────────────────────
class ProgressTracker:
    """Encapsulates the repeated progress-bar + ETA + metrics pattern.

    Usage:
        tracker = ProgressTracker("Generating notes")
        # inside the generation loop, pass tracker.on_progress as callback
        tracker.finalize(completed_count, total_count)
    """

    def __init__(self, task_label: str, parent_status=None):
        self.task_label = task_label
        self.t0 = time.time()
        self._times: list[float] = []
        self._start = time.time()
        self.parent_status = parent_status

        # Create Streamlit widgets
        self.prog = st.progress(0)
        self.metrics_container = st.container()
        self.topic_label = st.empty()

        # Log start
        log_activity(f"Started: {task_label}", icon="🚀", level="progress")
        update_sidebar_progress(task=task_label)

    def on_progress(self, completed: int, total: int, message: str):
        """Standard callback signature: (completed, total, current_topic_msg)."""
        now = time.time()
        topic_dur = now - self._start
        if topic_dur > 0.5:
            self._times.append(topic_dur)
        self._start = now

        frac = completed / total if total else 0
        self.prog.progress(frac)

        elapsed = now - self.t0
        remaining = total - completed

        # Weighted moving average ETA
        if self._times:
            weights = [1.2 ** i for i in range(len(self._times))]
            avg_t = sum(w * d for w, d in zip(weights, self._times)) / sum(weights)
            eta = avg_t * remaining
        else:
            eta = (elapsed / completed * remaining) if completed > 0 else 0

        # Update main-area metrics
        with self.metrics_container:
            self.metrics_container.empty()
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("⏱️ Elapsed", fmt_duration(elapsed))
            mc2.metric("📊 Progress", f"{completed} / {total}")
            mc3.metric("⏳ ETA", "~" + fmt_duration(eta) if remaining > 0 else "Done!")
            last_d = self._times[-1] if self._times else topic_dur
            mc4.metric("🕐 Last Topic", fmt_duration(last_d))

        self.topic_label.markdown(f"🔄 **{message}**  &nbsp;·&nbsp; {frac*100:.0f}%")

        # Update sidebar progress
        update_sidebar_progress(
            task=self.task_label,
            frac=frac,
            label=f"{completed} / {total}",
            eta="~" + fmt_duration(eta) if remaining > 0 else "Done!",
            elapsed=fmt_duration(elapsed),
        )

    def finalize(self, completed: int, total: int, extra_metric: tuple | None = None):
        """Show final summary metrics and log completion."""
        elapsed_total = time.time() - self.t0
        self.prog.progress(1.0)

        with self.metrics_container:
            self.metrics_container.empty()
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("⏱️ Total Time", fmt_duration(elapsed_total))
            mc2.metric("📊 Completed", f"{completed} / {total}")
            avg = elapsed_total / completed if completed else 0
            mc3.metric("⚡ Avg / Topic", fmt_duration(avg))
            if extra_metric:
                mc4.metric(extra_metric[0], extra_metric[1])
            else:
                mc4.metric("✅ Status", "Complete!")

        self.topic_label.markdown(
            f"✅ **{self.task_label} — {completed} items in {fmt_duration(elapsed_total)}!**"
        )

        if self.parent_status:
            self.parent_status.update(
                label=f"✅ {self.task_label} — {fmt_duration(elapsed_total)}",
                state="complete",
            )

        log_activity(
            f"Completed: {self.task_label} ({completed} items, {fmt_duration(elapsed_total)})",
            icon="✅", level="success",
        )
        clear_sidebar_progress()
        return elapsed_total


# ── Utilities ────────────────────────────────────────────────────────────────
def topic_heading_level(topic_id: str) -> int:
    """Return the Markdown heading level for a topic (T1→1, T1.1→2, T1.1.1→3, …)."""
    return min(topic_id.count('.') + 1, 6)


def clean_topic_title(note: dict) -> str:
    """Return the topic title without the T1.x.x ID prefix."""
    return note.get("title", "Untitled")


def topic_display_label(note: dict) -> str:
    """Build a numbered display label: 'Chapter 1: Title' or '1.1 Title'."""
    import re as _re
    t_id = note.get("id", "")
    title = note.get("title", "Untitled")
    num = _re.sub(r'^[A-Za-z]+', '', t_id)
    depth = t_id.count('.')
    if depth == 0:
        return f"Chapter {num}: {title}"
    return f"{num}  {title}"


def _looks_like_json(text: str) -> bool:
    """Return True if text appears to be raw JSON rather than readable content."""
    stripped = text.strip()
    if stripped.startswith(('{', '[')) and stripped.endswith(('}', ']')):
        return True
    if '"type":' in stripped and '"content":' in stripped:
        return True
    return False


def _sanitize_element_content(text: str) -> str:
    """Extract readable text from content that contains JSON artifacts."""
    import re as _re
    pieces = []
    for m in _re.finditer(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text):
        piece = m.group(1).replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t')
        if len(piece.strip()) > 10:
            pieces.append(piece.strip())
    if pieces:
        return "\n\n".join(pieces)
    # Fallback: strip JSON syntax
    cleaned = _re.sub(r'[{}\[\]"]', '', text)
    cleaned = _re.sub(r'"?type"?\s*:\s*"?\w+"?,?', '', cleaned)
    cleaned = _re.sub(r'"?content"?\s*:', '', cleaned)
    cleaned = _re.sub(r'"?level"?\s*:\s*\d+,?', '', cleaned)
    cleaned = _re.sub(r'"?callout_?type"?\s*:\s*"?[^"]*"?,?', '', cleaned)
    cleaned = _re.sub(r'"?elements"?\s*:', '', cleaned)
    cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned or text


# ── Render note elements with native Streamlit widgets ───────────────────────
def render_note_elements(note: dict, heading_offset: int = 0):
    """Render structured note elements using native Streamlit widgets.

    Args:
        note: Dict with 'elements' list.
        heading_offset: Added to each element heading level so inner headings
                        sit below the topic's own heading.
    """
    for el in note.get("elements", []):
        t = el.get("type")
        c = el.get("content", "")

        # Guard: if content looks like raw JSON, sanitize it
        if t == "text" and _looks_like_json(c):
            c = _sanitize_element_content(c)

        if t == "heading":
            level = min(el.get("level", 2) + heading_offset, 6)
            st.markdown(f"{'#' * level} {c}")
        elif t == "callout":
            ctype = el.get("callout_type", "Note")
            st.markdown(f"> **{ctype}**\n> {c}")
        elif t == "code":
            lang = el.get("language", "")
            st.code(c, language=lang if lang else None)
        elif t == "equation":
            st.latex(c)
        elif t == "image_placeholder":
            query = el.get("search_query", "")
            caption = el.get("caption", "")
            local_path = el.get("image_local_path", "")
            url = el.get("image_url", "")
            displayed = False
            if local_path and os.path.isfile(local_path):
                try:
                    st.image(local_path, caption=f"Figure: {caption}", use_container_width=True)
                    displayed = True
                except Exception as img_err:
                    logger.warning(f"Failed to display local image {local_path}: {img_err}")
            if not displayed and url:
                try:
                    st.image(url, caption=f"Figure: {caption}", use_container_width=True)
                    displayed = True
                except Exception:
                    pass
            if not displayed:
                st.info(f"🖼️ **Image Placeholder**\n\n*Search Query:* {query}\n\n*Caption:* {caption}")
        elif t == "source_citation":
            st.markdown(f"> 📚 *Source: {c}*")
        elif t == "text":
            st.markdown(c)
        else:
            if c:
                st.markdown(c)


# ── Pause-aware progress callback wrapper ────────────────────────────────────
def make_pause_callback(tracker: ProgressTracker, pause_key: str, auto_save_every: int = 0):
    """Return a progress callback that checks for pause and optionally auto-saves.
    
    Args:
        tracker: The ProgressTracker handling metrics.
        pause_key: session_state key for the pause checkbox (e.g. "pause_lp").
        auto_save_every: Auto-save course state every N topics (0 = disabled).
    """
    def callback(completed, total, message):
        tracker.on_progress(completed, total, message)
        if st.session_state.get(pause_key):
            st.session_state[pause_key] = False
            raise PauseRequested()
        if auto_save_every and completed % auto_save_every == 0:
            save_course_state()
    return callback
