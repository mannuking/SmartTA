import streamlit as st
import json
import logging
import secrets
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime, timedelta
import sys

# Add project root to sys.path
current_file = Path(__file__).resolve()
root_dir = str(current_file.parents[2])
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)
from Smart_Teaching_Assistance.config import load_config_from_env, Config
from Smart_Teaching_Assistance.database import get_database

logger = logging.getLogger(__name__)

STEPS = [
    "Input Materials",
    "Generate Roadmap",
    "Lesson Plan",
    "Detailed Notes",
    "Personalize",
    "Export",
]
STEP_ICONS = ["📥", "🗺️", "📝", "📖", "👥", "📤"]
MAX_BOOK_TOKENS = 500_000

def _defaults():
    return dict(
        config=None, db=None,
        gemini_api_key="",               # Current active API key
        gemini_api_keys=[],              # List of all available API keys
        step=-1,                         # Start at dashboard
        # Step 1 — Input
        subject_name="",
        syllabus_text="",
        pdf_uploaded=False, pdf_filename=None,
        pdf_text="", pdf_token_count=0,
        use_book_context=False,
        pdf_hash=None,
        extracted_images=[],
        # PDF-Extract-Kit RAG corpus (Phase A ingest output)
        course_corpus=None,            # CourseCorpus pickle
        courses_dir="",                # path on disk where chroma + corpus live
        rag_ready=False,               # True after first successful index_corpus() call
        # Step 2 — Roadmap
        roadmap_generated=False,
        roadmap_text="", roadmap_tree={}, roadmap_structure={}, roadmap_id=None,
        difficulty="intermediate",
        # Step 3 — Lesson Plan
        lesson_plan_generated=False,
        lesson_plan={},                 # full JSON tree
        lp_editing={},                  # track which topics are in edit mode
        # Step 4 — Detailed Notes
        detailed_notes_generated=False,
        all_notes_data=[],              # list of {id, title, elements}
        answer_key_text=None,
        notes_markdown="",
        highlighted_topics_input="",
        # Step 5 — Personalize
        personalized_versions=[],
        # Step 6 — Export (stateless)
        _nav_pending=None,
        active_note_id=None,
        course_id=None,
        is_paused=False,
        audience_input_val="",           # For step 5
        pause_lp=False,                   # Step 3 pause flag
        pause_notes=False,                # Step 4 pause flag
        notes_worker=None,                # BackgroundNotesWorker instance
        lp_worker=None,                      # BackgroundLPWorker instance
        generation_status="idle",             # idle | generating_lp | generating_notes | paused_lp | paused_notes
        # Phase 2 — Exam Prep Features
        chapter_summaries={},             # {chapter_id: {title, summary, key_concepts, ...}}
        flashcards=[],                    # list of {front, back, topic_id, ...}
        question_bank=[],                 # list of {question, answer, topic_id, bloom_level, ...}
        practice_exam=None,               # {title, instructions, questions, answer_key}
        glossary=[],                      # list of {term, definition, source_topic_id, ...}
        review_completed=False,           # Whether post-generation review pass has run
        # ── Sidebar Activity Log ──
        activity_log=[],                  # list of {ts, icon, msg, level}
        sidebar_current_task="",          # What's happening right now
        sidebar_progress_frac=0.0,        # 0.0-1.0 progress
        sidebar_progress_label="",        # e.g. "3 / 12 topics"
        sidebar_eta="",                   # estimated time remaining
        sidebar_elapsed="",               # elapsed time string
        sidebar_last_error="",            # last error message for debug
        sidebar_show_debug=False,         # toggle debug panel
        user_id=None,                     # For multi-tenancy
        username=None,
        auth_token=None,
        _user_bypassed_autoload=False,    # Set by Reset and "Create New
                                          # Course". When True, the
                                          # Dashboard will show the
                                          # course list without auto-
                                          # loading the most recent one.
                                          # The flag is cleared once the
                                          # user explicitly clicks a
                                          # course card or finishes
                                          # creating a new one.
    )

def init_state():
    for k, v in _defaults().items():
        if k not in st.session_state:
            st.session_state[k] = v

    # Recover login on browser refresh using persisted auth token.
    if st.session_state.get("user_id") is None:
        _restore_auth_from_query_token()

    if st.session_state.get("_nav_pending") is not None:
        target = st.session_state._nav_pending
        st.session_state._nav_pending = None
        st.session_state.step = target
    # Auto-load global settings (like API key)
    _load_global_settings()
    # Auto-load last active course on fresh session (e.g. browser refresh)
    _auto_load_last_course()


def _get_query_param_value(name: str) -> str:
    """Get a query parameter as a scalar string."""
    try:
        value = st.query_params.get(name, "")
    except Exception:
        return ""

    if isinstance(value, list):
        return value[0] if value else ""
    return str(value or "")


def create_auth_session(user_id: int, username: str, ttl_days: int = 14) -> str:
    """Create persistent auth token and place it in query params."""
    token = secrets.token_urlsafe(32)
    db = get_db()
    expires_at = (datetime.utcnow() + timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")

    db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    db.execute(
        "INSERT INTO auth_sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires_at),
    )

    st.session_state.auth_token = token
    st.query_params["auth"] = token
    log_activity(f"Persistent session created for {username}", icon="🔐", level="info")
    return token


def _restore_auth_from_query_token() -> bool:
    """Restore user session from query token after refresh/new tab."""
    token = _get_query_param_value("auth")
    if not token:
        return False

    try:
        db = get_db()
        db.execute("DELETE FROM auth_sessions WHERE expires_at <= CURRENT_TIMESTAMP")
        row = db.fetchone(
            """
            SELECT s.token, s.user_id, u.username
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at > CURRENT_TIMESTAMP
            """,
            (token,),
        )
        if not row:
            st.query_params.pop("auth", None)
            return False

        st.session_state.user_id = row["user_id"]
        st.session_state.username = row["username"]
        st.session_state.auth_token = row["token"]
        return True
    except Exception as e:
        logger.debug(f"Auth token restore skipped: {e}")
        return False


def clear_auth_session():
    """Clear persistent auth token from DB and query params."""
    token = st.session_state.get("auth_token") or _get_query_param_value("auth")
    if token:
        try:
            db = get_db()
            db.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
        except Exception as e:
            logger.debug(f"Auth token delete skipped: {e}")

    st.query_params.pop("auth", None)
    st.session_state.auth_token = None


def _load_global_settings():
    """Load user-specific global settings like API keys."""
    user_id = st.session_state.get("user_id")
    if user_id is None:
        return

    try:
        db = get_db()
        # Load multiple API keys for THIS user
        rows = db.fetchall(
            "SELECT key_value FROM api_keys WHERE user_id = ? AND is_active = 1 ORDER BY created_at ASC", 
            (user_id,)
        )
        keys = [r["key_value"] for r in rows]
        st.session_state.gemini_api_keys = keys
        
        # Set initial active key if not set
        if keys and not st.session_state.get("gemini_api_key"):
            st.session_state.gemini_api_key = keys[0]
            st.session_state.config = None
            logger.info(f"Loaded {len(keys)} API keys for user {user_id}")
    except Exception as e:
        logger.debug(f"Global settings load skipped: {e}")


def _auto_load_last_course():
    """Auto-load the last active course for the logged-in user.

    Skipped if:
      - step != -1 (user is mid-pipeline -- respect that, don't override)
      - course_id is already set (already loaded)
      - subject_name is set (unsaved in-progress course)
      - _user_bypassed_autoload is True (the user just clicked Reset
        or "Create New Course" -- they want a clean Dashboard)

    The last guard is the fix for the "Reset / Create New Course is
    a no-op" bug: the previous implementation auto-loaded on every
    Dashboard render, so even after reset_session() cleared state,
    the next render would reload the most-recent course behind the
    user's back.
    """
    if st.session_state.get("_user_bypassed_autoload"):
        return
    if st.session_state.get("step") != -1:
        return
    if st.session_state.get("course_id") is not None:
        _reconnect_workers(st.session_state.course_id)
        return
    if st.session_state.get("subject_name"):
        return  # new unsaved course in progress
    
    user_id = st.session_state.get("user_id")
    if user_id is None:
        return

    try:
        db = get_db()
        row = db.fetchone(
            "SELECT id FROM courses WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1", 
            (user_id,)
        )
        if row:
            load_course_state(row["id"])
            logger.info(f"Auto-loaded course {row['id']} for user {user_id}")
            _reconnect_workers(row["id"])
    except Exception as e:
        logger.debug(f"Auto-load skipped: {e}")


def _reconnect_workers(course_id: int):
    """Re-attach any still-alive workers from the process-level registry."""
    from Smart_Teaching_Assistance.ui.background_worker import find_worker

    for wtype, skey in [("notes", "notes_worker"), ("lp", "lp_worker")]:
        existing = st.session_state.get(skey)
        # Skip if session already has a non-idle worker
        if existing and hasattr(existing, "is_running") and (existing.is_running or existing.status != "idle"):
            continue
        found = find_worker(course_id, wtype)
        if found is not None and (found.is_running or found.status in ("completed", "paused", "error")):
            st.session_state[skey] = found
            logger.info(f"Reconnected {wtype} worker for course {course_id} (status={found.status})")

def go(step: int):
    st.session_state._nav_pending = step

def get_config() -> Config:
    cfg = st.session_state.get("config")
    if cfg is None:
        try:
            from dotenv import load_dotenv; load_dotenv()
        except ImportError:
            pass
        # Use API key from session (user-entered) or env var
        api_key = st.session_state.get("gemini_api_key", "")
        user_id = st.session_state.get("user_id")
        cfg = load_config_from_env(api_key_override=api_key or None, user_id=user_id)
        st.session_state.config = cfg
    return cfg


def has_api_key() -> bool:
    """Return True if at least one Gemini API key is available."""
    if st.session_state.get("gemini_api_keys"):
        return True
    if st.session_state.get("gemini_api_key"):
        return True
    
    # Check environment variable
    import os
    if os.getenv("GEMINI_API_KEY"):
        return True
        
    # Check database as last resort
    try:
        db = get_db()
        user_id = st.session_state.get("user_id")
        if user_id is not None:
            row = db.fetchone(
                "SELECT COUNT(*) as count FROM api_keys WHERE user_id = ? AND is_active = 1",
                (user_id,)
            )
            if row and row["count"] > 0:
                return True
        # Fallback to old settings table
        row = db.fetchone("SELECT value FROM settings WHERE key = 'gemini_api_key'")
        if row and row["value"]:
            return True
    except Exception:
        pass
        
    return False


def get_all_api_keys() -> List[str]:
    """Return list of all configured API keys."""
    return st.session_state.get("gemini_api_keys", [])


def add_api_key(key: str) -> bool:
    """Add a new API key to the rotation.

    Returns:
        True if a new key was added, False otherwise.
    """
    clean_key = key.strip()
    if not clean_key:
        return False

    # Basic Gemini key sanity check to prevent accidental junk input.
    if not clean_key.startswith("AIza") or len(clean_key) < 20:
        log_error("Invalid API key format")
        return False
    
    if clean_key in st.session_state.gemini_api_keys:
        log_activity("API key already exists", icon="ℹ️", level="info")
        return False

    st.session_state.gemini_api_keys.append(clean_key)
    if not st.session_state.gemini_api_key:
        st.session_state.gemini_api_key = clean_key
        st.session_state.config = None

    try:
        db = get_db()
        user_id = st.session_state.get("user_id")
        db.execute(
            "INSERT OR REPLACE INTO api_keys (key_value, user_id, is_active) VALUES (?, ?, 1)",
            (clean_key, user_id)
        )
        log_activity(f"Added API key {clean_key[:8]}...", icon="🔑", level="success")
        return True
    except Exception as e:
        log_error(f"Failed to save API key: {e}")
        return False


def remove_api_key(key: str):
    """Remove an API key from rotation."""
    if key in st.session_state.gemini_api_keys:
        st.session_state.gemini_api_keys.remove(key)
    
    if st.session_state.gemini_api_key == key:
        st.session_state.gemini_api_key = st.session_state.gemini_api_keys[0] if st.session_state.gemini_api_keys else ""
        st.session_state.config = None

    try:
        db = get_db()
        user_id = st.session_state.get("user_id")
        db.execute("DELETE FROM api_keys WHERE key_value = ? AND user_id = ?", (key, user_id))
        # Also clean up from old settings table if it was there
        db.execute("DELETE FROM settings WHERE key = 'gemini_api_key' AND value = ?", (key,))
        log_activity(f"Removed API key {key[:8]}...", icon="🗑️")
    except Exception as e:
        log_error(f"Failed to delete API key: {e}")


def rotate_api_key() -> Optional[str]:
    """Rotate to the next available API key in the list and return it."""
    keys = st.session_state.gemini_api_keys
    if len(keys) <= 1:
        return None
    
    current = st.session_state.gemini_api_key
    try:
        idx = keys.index(current)
        next_idx = (idx + 1) % len(keys)
        new_key = keys[next_idx]
        st.session_state.gemini_api_key = new_key
        st.session_state.config = None
        log_activity(f"Rotated API key to {new_key[:8]}...", icon="🔄", level="info")
        return new_key
    except ValueError:
        if keys:
            new_key = keys[0]
            st.session_state.gemini_api_key = new_key
            st.session_state.config = None
            return new_key
    return None


def set_api_key(key: str):
    """Legacy helper for single-key updates, now maps to add_api_key."""
    add_api_key(key)

def get_db(config=None):
    db_inst = st.session_state.get("db")
    if db_inst is None:
        if config is None:
            config = get_config()
        db_inst = get_database(config.db_path)
        st.session_state.db = db_inst
    try:
        db_inst.connect()
    except Exception:
        if config is None:
            config = get_config()
        db_inst = get_database(config.db_path)
        st.session_state.db = db_inst
    return db_inst

def save_course_state():
    if not st.session_state.subject_name:
        return
    db = get_db()
    persist_keys = [
        "step", "subject_name", "syllabus_text", "pdf_uploaded", "pdf_filename",
        "pdf_text", "pdf_token_count", "use_book_context", "pdf_hash", "extracted_images",
        "roadmap_generated", "roadmap_text", "roadmap_tree", "roadmap_structure", "roadmap_id",
        "difficulty", "lesson_plan_generated", "lesson_plan", "lp_editing",
        "detailed_notes_generated", "all_notes_data", "answer_key_text", "notes_markdown",
        "highlighted_topics_input", "personalized_versions", "active_note_id",
        "chapter_summaries", "flashcards", "question_bank", "practice_exam",
        "glossary", "review_completed", "generation_status"
    ]
    state_to_save = {k: st.session_state[k] for k in persist_keys if k in st.session_state}
    state_json = json.dumps(state_to_save, default=str)
    user_id = st.session_state.get("user_id")
    if st.session_state.course_id:
        db.execute(
            "UPDATE courses SET subject_name = ?, current_step = ?, state_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (st.session_state.subject_name, st.session_state.step, state_json, st.session_state.course_id, user_id)
        )
    else:
        cursor = db.execute(
            "INSERT INTO courses (user_id, subject_name, current_step, state_json) VALUES (?, ?, ?, ?)",
            (user_id, st.session_state.subject_name, st.session_state.step, state_json)
        )
        st.session_state.course_id = cursor.lastrowid

def load_course_state(course_id):
    db = get_db()
    user_id = st.session_state.get("user_id")
    row = db.fetchone("SELECT * FROM courses WHERE id = ? AND user_id = ?", (course_id, user_id))
    if row:
        state_data = json.loads(row["state_json"])
        for k, v in state_data.items():
            st.session_state[k] = v
        st.session_state.course_id = row["id"]
        st.session_state.step = row["current_step"]
        # User explicitly chose this course -- clear any pending
        # autoload-bypass so the Dashboard's auto-load behavior
        # resumes on the next visit.
        st.session_state["_user_bypassed_autoload"] = False
        return True
    return False

def delete_course(course_id):
    db = get_db()
    user_id = st.session_state.get("user_id")
    db.execute("DELETE FROM courses WHERE id = ? AND user_id = ?", (course_id, user_id))
    if st.session_state.course_id == course_id:
        for k, v in _defaults().items():
            st.session_state[k] = v

def reset_session():
    """Wipe the in-memory session and mark "user bypassed autoload".

    After Reset, the user expects to land on an empty Dashboard --
    not to have the most-recent course silently re-loaded on the next
    render. So we set _user_bypassed_autoload=True. The flag is
    cleared automatically the next time the user does ANY of:
      - clicks a course card to load it
      - clicks "Create New Course" and lands on Step 1
      - completes a new course (subject_name is set)
    """
    for k, v in _defaults().items():
        st.session_state[k] = v
    st.session_state["_user_bypassed_autoload"] = True


def clear_autoload_bypass():
    """Clear the _user_bypassed_autoload flag.

    Call this once the user has committed to a new path (clicked a
    course card, or is on Step 1 starting a fresh pipeline) so the
    Dashboard will resume auto-loading on future visits.
    """
    st.session_state["_user_bypassed_autoload"] = False


# ── Sidebar Activity Log Helpers ─────────────────────────────────────────────

MAX_LOG_ENTRIES = 50

def log_activity(msg: str, icon: str = "📌", level: str = "info"):
    """Append an entry to the sidebar activity feed.
    
    Args:
        msg: Short human-readable message (e.g. "Roadmap generated in 12s")
        icon: Emoji icon for the log entry
        level: One of "info", "success", "warning", "error", "progress"
    """
    ts = datetime.now().strftime("%H:%M:%S")
    entry = {"ts": ts, "icon": icon, "msg": msg, "level": level}
    if "activity_log" not in st.session_state:
        st.session_state.activity_log = []
    st.session_state.activity_log.insert(0, entry)  # newest first
    # Trim to keep memory bounded
    if len(st.session_state.activity_log) > MAX_LOG_ENTRIES:
        st.session_state.activity_log = st.session_state.activity_log[:MAX_LOG_ENTRIES]


def update_sidebar_progress(task: str = "", frac: float = 0.0, label: str = "",
                            eta: str = "", elapsed: str = ""):
    """Update the sidebar's live progress panel from any step callback."""
    if task:
        st.session_state.sidebar_current_task = task
    st.session_state.sidebar_progress_frac = max(0.0, min(1.0, frac))
    if label:
        st.session_state.sidebar_progress_label = label
    if eta:
        st.session_state.sidebar_eta = eta
    if elapsed:
        st.session_state.sidebar_elapsed = elapsed


def clear_sidebar_progress():
    """Reset the live progress indicators after a task finishes."""
    st.session_state.sidebar_current_task = ""
    st.session_state.sidebar_progress_frac = 0.0
    st.session_state.sidebar_progress_label = ""
    st.session_state.sidebar_eta = ""
    st.session_state.sidebar_elapsed = ""


def log_error(msg: str):
    """Log an error to both the activity feed and the debug panel."""
    st.session_state.sidebar_last_error = msg
    log_activity(msg, icon="❌", level="error")


def set_generation_status(status: str):
    """Update generation_status in session state AND persist to DB immediately.

    Values: 'idle', 'generating_lp', 'generating_notes', 'paused_lp', 'paused_notes'
    """
    st.session_state.generation_status = status
    save_course_state()
