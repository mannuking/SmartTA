"""
Background worker for long-running generation tasks.

Runs note generation in a daemon thread so navigating to other pages
does NOT cancel the process.  The main (Streamlit) thread polls a
thread-safe shared dict for progress / results.

A **process-level registry** (_WORKER_REGISTRY) keeps references to running
workers so that a browser refresh (which destroys st.session_state) can
reconnect to any worker still alive in the Python process.
"""

import copy
import threading
import time
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from Smart_Teaching_Assistance.error_handling import PauseRequested

logger = logging.getLogger(__name__)

# ── Process-level worker registry ────────────────────────────────────────────
# Keyed by (course_id, worker_type) where worker_type is "notes" or "lp".
# Survives st.session_state resets (browser refresh) because it lives at
# module scope in the long-running Streamlit server process.
_WORKER_REGISTRY: Dict[tuple, Any] = {}
_REGISTRY_LOCK = threading.Lock()


def register_worker(course_id: int, worker_type: str, worker):
    """Store a running worker so it can be found after session reset."""
    with _REGISTRY_LOCK:
        _WORKER_REGISTRY[(course_id, worker_type)] = worker
        logger.info(f"Registered {worker_type} worker for course {course_id}")


def unregister_worker(course_id: int, worker_type: str):
    """Remove a worker from the registry (call on completion/error/reset)."""
    with _REGISTRY_LOCK:
        removed = _WORKER_REGISTRY.pop((course_id, worker_type), None)
        if removed:
            logger.info(f"Unregistered {worker_type} worker for course {course_id}")


def find_worker(course_id: int, worker_type: str):
    """Look up a worker that may still be alive from a previous session."""
    with _REGISTRY_LOCK:
        return _WORKER_REGISTRY.get((course_id, worker_type))


class BackgroundNotesWorker:
    """Run note generation in a background thread.

    Lifecycle:
        worker.start(...)       →  spawns thread
        worker.progress         →  read-only snapshot  (polled by UI)
        worker.pause() / .resume()
        worker.results          →  final (all_notes, answer_key) when done
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._pause_event = threading.Event()       # .is_set() → paused
        self._stop_event = threading.Event()        # .is_set() → abort

        self._progress: Dict[str, Any] = {
            "status": "idle",       # idle | running | paused | completed | error
            "completed": 0,
            "total": 0,
            "message": "",
            "frac": 0.0,
            "elapsed": 0.0,
            "eta": 0.0,
            "eta_str": "",
            "elapsed_str": "",
            "label": "",
            "error": None,
        }

        self._results: Optional[Tuple[List[Dict], Optional[str]]] = None
        # Snapshot of all_notes accumulated so far (for live sync)
        self._partial_notes: List[Dict] = []
        self._t0: float = 0.0
        self._times: List[float] = []
        self._topic_start: float = 0.0

    # ── Public properties ────────────────────────────────────────────────
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    @property
    def status(self) -> str:
        with self._lock:
            return self._progress["status"]

    @property
    def progress(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._progress)

    @property
    def results(self) -> Optional[Tuple[List[Dict], Optional[str]]]:
        with self._lock:
            return self._results

    @property
    def partial_notes(self) -> List[Dict]:
        with self._lock:
            return list(self._partial_notes)

    # ── Control ──────────────────────────────────────────────────────────
    def start(
        self,
        gen_callable: Callable,
        gen_kwargs: Dict[str, Any],
        existing_notes: Optional[List[Dict]] = None,
    ) -> bool:
        """Start (or re-start) the generation in a background thread."""
        if self.is_running:
            return False

        self._pause_event.clear()
        self._stop_event.clear()
        self._results = None
        self._partial_notes = list(existing_notes or [])
        self._times = []
        self._t0 = time.time()
        self._topic_start = time.time()

        with self._lock:
            self._progress = {
                "status": "running",
                "completed": len(self._partial_notes),
                "total": 0,
                "message": "Starting...",
                "frac": 0.0,
                "elapsed": 0.0,
                "eta": 0.0,
                "eta_str": "",
                "elapsed_str": "",
                "label": "",
                "error": None,
            }

        gen_kwargs["progress_cb"] = self._progress_callback

        self._thread = threading.Thread(
            target=self._run, args=(gen_callable, gen_kwargs), daemon=True
        )
        self._thread.start()
        logger.info("Background notes worker started")
        return True

    def pause(self):
        """Request pause — the generation will stop after the current topic."""
        self._pause_event.set()
        with self._lock:
            self._progress["status"] = "pausing"

    def resume(self):
        """Clear pause flag (only effective before the thread checks it)."""
        self._pause_event.clear()

    def stop(self):
        """Request a full stop."""
        self._stop_event.set()
        self._pause_event.set()          # also unblocks any pause-wait

    # ── Internal: thread entry point ─────────────────────────────────────
    def _run(self, gen_callable: Callable, gen_kwargs: Dict[str, Any]):
        try:
            all_notes, answer_key = gen_callable(**gen_kwargs)
            with self._lock:
                self._results = (all_notes, answer_key)
                self._partial_notes = list(all_notes)
                self._progress["status"] = "completed"
                self._progress["frac"] = 1.0
                self._progress["message"] = "Completed!"
            logger.info("Background notes worker finished successfully")
        except PauseRequested:
            with self._lock:
                self._progress["status"] = "paused"
                self._progress["message"] = "Paused by user"
            logger.info("Background notes worker paused")
        except Exception as exc:
            with self._lock:
                self._progress["status"] = "error"
                self._progress["error"] = str(exc)
                self._progress["message"] = f"Error: {exc}"
            logger.exception("Background notes worker error")

    # ── Internal: progress callback (called FROM worker thread) ──────────
    def _progress_callback(self, completed: int, total: int, message: str):
        now = time.time()
        elapsed = now - self._t0

        # Convention: completed == -1 signals the "image fetching" phase.
        # In this phase we don't update topic timing / ETA — just show the message.
        if completed == -1:
            with self._lock:
                self._progress.update({
                    "status": "running",
                    "phase": "images",
                    "completed": self._progress.get("completed", 0),  # keep notes count
                    "total": self._progress.get("total", 0),
                    "message": message,
                    "frac": 1.0,  # notes are done
                    "elapsed": elapsed,
                    "eta": 0,
                    "eta_str": "Fetching images...",
                    "elapsed_str": _fmt(elapsed),
                    "label": message,
                })
            return

        topic_dur = now - self._topic_start
        if topic_dur > 0.5:
            self._times.append(topic_dur)
        self._topic_start = now

        frac = completed / total if total else 0
        remaining = total - completed

        # Weighted moving average ETA
        if self._times:
            weights = [1.2 ** i for i in range(len(self._times))]
            avg_t = sum(w * d for w, d in zip(weights, self._times)) / sum(weights)
            eta = avg_t * remaining
        elif completed > 0:
            eta = elapsed / completed * remaining
        else:
            eta = 0

        with self._lock:
            self._progress.update({
                "status": "running",
                "phase": "notes",
                "completed": completed,
                "total": total,
                "message": message,
                "frac": frac,
                "elapsed": elapsed,
                "eta": eta,
                "eta_str": "~" + _fmt(eta) if remaining > 0 else "Done!",
                "elapsed_str": _fmt(elapsed),
                "label": f"{completed} / {total}",
            })

        # Check pause / stop
        if self._stop_event.is_set():
            raise PauseRequested()
        if self._pause_event.is_set():
            raise PauseRequested()

    # ── Snapshot updater (called from generation to keep partial_notes) ──
    def update_partial(self, all_notes: List[Dict]):
        with self._lock:
            self._partial_notes = list(all_notes)


class BackgroundLPWorker:
    """Run lesson-plan generation in a background thread.

    Mirrors BackgroundNotesWorker but stores a partial *plan dict* instead of
    a notes list, and the final result is a single dict (the full lesson plan).

    Lifecycle:
        worker.start(...)       →  spawns thread
        worker.progress         →  read-only snapshot  (polled by UI)
        worker.pause() / .resume()
        worker.results          →  final lesson_plan dict when done
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()

        self._progress: Dict[str, Any] = {
            "status": "idle",
            "completed": 0,
            "total": 0,
            "message": "",
            "frac": 0.0,
            "elapsed": 0.0,
            "eta": 0.0,
            "eta_str": "",
            "elapsed_str": "",
            "label": "",
            "error": None,
        }

        self._results: Optional[Dict] = None
        self._partial_plan: Dict = {}
        self._t0: float = 0.0
        self._times: List[float] = []
        self._topic_start: float = 0.0

    # ── Public properties ────────────────────────────────────────────────
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    @property
    def status(self) -> str:
        with self._lock:
            return self._progress["status"]

    @property
    def progress(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._progress)

    @property
    def results(self) -> Optional[Dict]:
        with self._lock:
            return self._results

    @property
    def partial_plan(self) -> Dict:
        with self._lock:
            return copy.deepcopy(self._partial_plan) if self._partial_plan else {}

    # ── Control ──────────────────────────────────────────────────────────
    def start(
        self,
        gen_callable: Callable,
        gen_kwargs: Dict[str, Any],
        existing_plan: Optional[Dict] = None,
    ) -> bool:
        """Start (or re-start) the generation in a background thread."""
        if self.is_running:
            return False

        self._pause_event.clear()
        self._stop_event.clear()
        self._results = None
        self._partial_plan = dict(existing_plan) if existing_plan else {}
        self._times = []
        self._t0 = time.time()
        self._topic_start = time.time()

        with self._lock:
            self._progress = {
                "status": "running",
                "completed": 0,
                "total": 0,
                "message": "Starting...",
                "frac": 0.0,
                "elapsed": 0.0,
                "eta": 0.0,
                "eta_str": "",
                "elapsed_str": "",
                "label": "",
                "error": None,
            }

        gen_kwargs["progress_cb"] = self._progress_callback

        self._thread = threading.Thread(
            target=self._run, args=(gen_callable, gen_kwargs), daemon=True
        )
        self._thread.start()
        logger.info("Background LP worker started")
        return True

    def pause(self):
        self._pause_event.set()
        with self._lock:
            self._progress["status"] = "pausing"

    def resume(self):
        self._pause_event.clear()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()

    # ── Internal: thread entry point ─────────────────────────────────────
    def _run(self, gen_callable: Callable, gen_kwargs: Dict[str, Any]):
        try:
            lesson_plan = gen_callable(**gen_kwargs)
            with self._lock:
                self._results = lesson_plan
                self._partial_plan = dict(lesson_plan)
                self._progress["status"] = "completed"
                self._progress["frac"] = 1.0
                self._progress["message"] = "Completed!"
            logger.info("Background LP worker finished successfully")
        except PauseRequested:
            with self._lock:
                self._progress["status"] = "paused"
                self._progress["message"] = "Paused by user"
            logger.info("Background LP worker paused")
        except Exception as exc:
            with self._lock:
                self._progress["status"] = "error"
                self._progress["error"] = str(exc)
                self._progress["message"] = f"Error: {exc}"
            logger.exception("Background LP worker error")

    # ── Internal: progress callback (called FROM worker thread) ──────────
    def _progress_callback(self, completed: int, total: int, message: str):
        now = time.time()
        topic_dur = now - self._topic_start
        if topic_dur > 0.5:
            self._times.append(topic_dur)
        self._topic_start = now

        frac = completed / total if total else 0
        elapsed = now - self._t0
        remaining = total - completed

        if self._times:
            weights = [1.2 ** i for i in range(len(self._times))]
            avg_t = sum(w * d for w, d in zip(weights, self._times)) / sum(weights)
            eta = avg_t * remaining
        elif completed > 0:
            eta = elapsed / completed * remaining
        else:
            eta = 0

        with self._lock:
            self._progress.update({
                "status": "running",
                "completed": completed,
                "total": total,
                "message": message,
                "frac": frac,
                "elapsed": elapsed,
                "eta": eta,
                "eta_str": "~" + _fmt(eta) if remaining > 0 else "Done!",
                "elapsed_str": _fmt(elapsed),
                "label": f"{completed} / {total}",
            })

        if self._stop_event.is_set():
            raise PauseRequested()
        if self._pause_event.is_set():
            raise PauseRequested()

    # ── Snapshot updater (called from generation to keep partial_plan) ───
    def update_partial(self, plan: Dict):
        with self._lock:
            self._partial_plan = copy.deepcopy(plan)


def _fmt(secs: float) -> str:
    """Format seconds -> human-readable string.

    Sub-second precision below 60s so the live counter actually moves
    (e.g. "21.4s" -> "21.9s" -> "22.3s") instead of freezing for a full
    second on the int boundary.
    """
    secs = max(0.0, float(secs))
    if secs < 60:
        # show 1 decimal for sub-minute timers so changes are visible immediately
        return f"{secs:.1f}s"
    total = int(secs)
    if total < 3600:
        m, s = divmod(total, 60)
        return f"{m}m {s:02d}s"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s"


# ============================================================================
# Roadmap worker -- single-shot generation in a thread.
# ============================================================================

class BackgroundRoadmapWorker:
    """Run roadmap generation in a background thread.

    The roadmap step is a single AI call (no per-topic loop), so the
    progress model is simpler than BackgroundLPWorker: a phase string
    ("preparing", "calling_ai", "parsing", "storing", "done") and a
    free-form message. The elapsed timer ticks every fragment refresh
    because the worker thread doesn't block the main script.

    Lifecycle:
        worker.start(...)        →  spawns thread
        worker.progress          →  read-only snapshot
        worker.results           →  final result dict when done
        worker.pause()           →  not applicable for roadmap (no-op)
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()

        self._progress: Dict[str, Any] = {
            "status": "idle",          # idle | running | completed | error
            "phase": "idle",           # idle | preparing | calling_ai | parsing | storing | done
            "message": "",
            "elapsed": 0.0,
            "elapsed_str": "",
            "error": None,
        }
        self._results: Optional[Dict[str, Any]] = None
        self._t0: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def status(self) -> str:
        with self._lock:
            return self._progress["status"]

    @property
    def progress(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._progress)

    @property
    def results(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._results

    def start(
        self,
        gen_callable: Callable,
        gen_kwargs: Dict[str, Any],
    ) -> bool:
        """Start (or re-start) the roadmap generation in a background thread."""
        if self.is_running:
            return False

        self._pause_event.clear()
        self._stop_event.clear()
        self._results = None
        self._t0 = time.time()

        with self._lock:
            self._progress = {
                "status": "running",
                "phase": "preparing",
                "message": "Preparing source...",
                "elapsed": 0.0,
                "elapsed_str": "0.0s",
                "error": None,
            }

        self._thread = threading.Thread(
            target=self._run,
            args=(gen_callable, gen_kwargs),
            daemon=True,
        )
        self._thread.start()
        logger.info("Background roadmap worker started")
        return True

    def update_phase(self, phase: str, message: str = "") -> None:
        """Update the live progress phase/message from the worker thread.

        Called by the gen_callable (via a phase_cb in gen_kwargs) to
        report intermediate states like "Calling AI..." or "Parsing..."
        so the user sees what's happening.
        """
        with self._lock:
            self._progress["phase"] = phase
            self._progress["message"] = message
            self._progress["elapsed"] = time.time() - self._t0
            self._progress["elapsed_str"] = _fmt(self._progress["elapsed"])
            # Auto-promote "idle" -> "running" so the fragment can
            # render even before start() is called.
            if self._progress["status"] == "idle":
                self._progress["status"] = "running"

    def _run(self, gen_callable: Callable, gen_kwargs: Dict[str, Any]):
        try:
            result = gen_callable(**gen_kwargs)
            with self._lock:
                self._results = result
                self._progress["status"] = "completed"
                self._progress["phase"] = "done"
                self._progress["message"] = "Completed!"
                self._progress["elapsed"] = time.time() - self._t0
                self._progress["elapsed_str"] = _fmt(self._progress["elapsed"])
            logger.info("Background roadmap worker finished successfully")
        except Exception as exc:
            with self._lock:
                self._progress["status"] = "error"
                self._progress["error"] = str(exc)
                self._progress["message"] = f"Error: {exc}"
            logger.exception("Background roadmap worker error")
