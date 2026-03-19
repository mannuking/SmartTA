"""
High-performance, multi-threaded image scraper for DuckDuckGo search results.

Architecture:
  1. SEARCH phase  – Sequential DDG queries with a single reused client,
                     staggered by a minimum interval.  Collects candidate
                     URLs for every placeholder.
  2. DOWNLOAD phase – Parallel HTTP downloads via a ThreadPoolExecutor.
                      Downloads hit many different image-hosting servers,
                      so parallelism is safe and fast.

Both phases report progress through an optional callback so the UI can
show a live "Fetching images" indicator.
"""

import os
import re
import time
import random
import hashlib
import logging
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

try:
    from ddgs import DDGS
except ImportError:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        from duckduckgo_search import DDGS

# Suppress noisy rename warnings from legacy duckduckgo_search package.
warnings.filterwarnings(
    "ignore",
    message=r".*duckduckgo_search.*renamed to.*ddgs.*",
    category=RuntimeWarning,
)

from .constants import DOWNLOAD_DIR

logger = logging.getLogger(__name__)

# ── Module-level DDG rate-limiter (shared across all callers) ────────────────

_ddg_lock = threading.Lock()
_last_ddg_request: float = 0.0
_DDG_MIN_INTERVAL: float = 0.9          # keep DDG tries near ~1 req/sec max
_DDG_BACKOFF_UNTIL: float = 0.0         # sleep until this epoch if rate-limited
def _ddg_wait():
    """Block until we're allowed to hit DDG again."""
    global _last_ddg_request
    with _ddg_lock:
        now = time.time()
        if now < _DDG_BACKOFF_UNTIL:
            wait = _DDG_BACKOFF_UNTIL - now
            logger.debug(f"DDG backoff: sleeping {wait:.1f}s")
            time.sleep(wait)
            now = time.time()
        elapsed = now - _last_ddg_request
        if elapsed < _DDG_MIN_INTERVAL:
            time.sleep(_DDG_MIN_INTERVAL - elapsed + random.uniform(0.5, 2.0))
        _last_ddg_request = time.time()


def _ddg_backoff(attempt: int):
    global _DDG_BACKOFF_UNTIL
    # Keep DDG backoff short; fail fast and let fallback provider handle results.
    delay = min(1.0, 0.25 * attempt + random.random() * 0.25)
    _DDG_BACKOFF_UNTIL = time.time() + delay
    logger.info(f"DDG rate-limited — quick backoff {delay:.2f}s (attempt {attempt})")


# ── Search ───────────────────────────────────────────────────────────────────

_DDG_SEARCH_RETRIES = 2


def _simplify_query(query: str, attempt: int) -> str:
    """Return progressively shorter query variants for later retry attempts.

    DDG caches vqd tokens per query string, so a 403'd vqd stays poisoned
    for the same exact query.  Changing the query forces a fresh vqd.
    """
    words = query.split()
    if attempt <= 1 or len(words) <= 3:
        return query
    if attempt == 2:
        # Drop last 25% of words
        cut = max(3, len(words) * 3 // 4)
        return " ".join(words[:cut])
    # attempt 3+: keep only first half
    cut = max(3, len(words) // 2)
    return " ".join(words[:cut])


def _search_images(query: str, max_results: int = 8) -> List[str]:
    """Return ranked list of candidate image URLs for *query*.

    Uses a fresh DDGS context manager per call to avoid stale-session 403s,
    and retries internally with exponential backoff.  On later retries the
    query is shortened to force a fresh DDG vqd token.
    """
    for attempt in range(_DDG_SEARCH_RETRIES):
        q = _simplify_query(query, attempt)
        _ddg_wait()
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(
                    q,
                    max_results=max_results,
                    safesearch="off",
                    region="wt-wt",
                ))
            if results:
                break
        except Exception as e:
            err_str = str(e)
            if "403" in err_str or "Ratelimit" in err_str:
                _ddg_backoff(attempt + 1)
                continue
            logger.warning(f"DDG search error for '{q[:60]}': {e}")
            if attempt >= _DDG_SEARCH_RETRIES - 1:
                return []
    else:
        return []

    preferred, others = [], []
    for r in results:
        url = r.get("image", "")
        if not url:
            continue
        low = url.lower().split("?")[0]
        if any(low.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
            preferred.append(url)
        else:
            others.append(url)
    return preferred + others


def _search_wikimedia_images(query: str, max_results: int = 8) -> List[str]:
    """Fallback search via Wikimedia Commons for faster reliability under DDG 403s."""
    try:
        sess = _get_session()
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,  # File namespace only
            "gsrlimit": str(max_results),
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "format": "json",
            "formatversion": "2",
        }
        resp = sess.get(
            "https://commons.wikimedia.org/w/api.php",
            params=params,
            timeout=8,
            headers={"User-Agent": random.choice(_USER_AGENTS)},
        )
        resp.raise_for_status()
        data = resp.json() or {}
        pages = ((data.get("query") or {}).get("pages") or [])
        urls: List[str] = []
        for page in pages:
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            url = info.get("url", "")
            mime = (info.get("mime") or "").lower()
            low = url.lower().split("?")[0]
            is_image_mime = mime.startswith("image/")
            is_image_ext = any(low.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp"))
            if url and (is_image_mime or is_image_ext):
                urls.append(url)
        return urls
    except Exception as e:
        logger.debug(f"Wikimedia search failed for '{query[:60]}': {e}")
        return []


# ── Download ─────────────────────────────────────────────────────────────────

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

_download_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def _get_session() -> requests.Session:
    """Return a reusable requests.Session (connection pooling)."""
    global _download_session
    if _download_session is None:
        with _session_lock:
            if _download_session is None:
                s = requests.Session()
                s.headers.update({
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=10, pool_maxsize=10,
                    max_retries=requests.adapters.Retry(
                        total=2, backoff_factor=0.3,
                        status_forcelist=[429, 500, 502, 503, 504],
                    ),
                )
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                _download_session = s
    return _download_session


def _download_one(url: str, query: str) -> Optional[str]:
    """Download a single image to DOWNLOAD_DIR.  Returns local path or None."""
    if not url or len(url) < 10 or not url.startswith("http"):
        return None

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-zA-Z0-9]+", "_", query)[:80].strip("_")
    ext = ".jpg"
    url_lower = url.lower().split("?")[0]
    for candidate_ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
        if url_lower.endswith(candidate_ext):
            ext = candidate_ext
            break
    filename = f"{slug}_{hashlib.md5(url.encode()).hexdigest()[:8]}{ext}"
    local_path = DOWNLOAD_DIR / filename

    if local_path.exists() and local_path.stat().st_size > 500:
        return str(local_path)

    try:
        sess = _get_session()
        resp = sess.get(
            url,
            timeout=12,
            headers={"User-Agent": random.choice(_USER_AGENTS)},
            stream=True,
        )
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "")
        if "image" not in ct and "octet-stream" not in ct:
            return None
        data = resp.content
        if len(data) < 500 or len(data) > 10 * 1024 * 1024:
            return None
        local_path.write_bytes(data)
        logger.info(f"Downloaded image → {local_path.name} ({len(data)} bytes)")
        return str(local_path)
    except Exception:
        return None


def _download_first_match(candidates: List[str], query: str) -> Optional[str]:
    """Try downloading candidate URLs in order, return first success."""
    for url in candidates[:5]:
        path = _download_one(url, query)
        if path:
            return path
    return None


# ── Public API ───────────────────────────────────────────────────────────────

def populate_images_parallel(
    all_notes: List[Dict],
    download_workers: int = 6,
    progress_cb: Optional[Callable] = None,
):
    """Two-phase parallel image population.

    Phase 1 (sequential): DDG search for each placeholder → candidate URLs.
                           Each search retries internally up to 5 times.
    Phase 2 (parallel):   Download first valid image per placeholder.

    Args:
        all_notes:  List of topic dicts (mutated in-place).
        download_workers: Thread-pool size for parallel downloads.
        progress_cb: ``(done, total, message)`` callback.
    """
    # ── Collect placeholders needing images ───────────────────────────
    placeholders: List[Dict] = []
    for topic in all_notes:
        for el in topic.get("elements", []):
            if el.get("type") != "image_placeholder":
                continue
            local = el.get("image_local_path", "")
            if local and os.path.isfile(local):
                continue
            if el.get("search_query"):
                placeholders.append(el)

    if not placeholders:
        logger.info("No image placeholders to populate.")
        if progress_cb:
            progress_cb(0, 0, "No images needed")
        return

    total = len(placeholders)
    logger.info(f"Image pipeline: {total} placeholders to fill")
    if progress_cb:
        progress_cb(0, total, f"Searching for {total} images...")

    # ── Phase 1: DDG searches (sequential, rate-limited) ─────────────
    search_results: Dict[int, List[str]] = {}  # idx → candidate URLs
    consecutive_failures = 0

    for idx, el in enumerate(placeholders):
        query = el["search_query"]

        if consecutive_failures >= 6:
            logger.warning(
                f"Skipping remaining {total - idx} DDG searches — "
                f"too many consecutive failures"
            )
            break

        candidates = _search_images(query, max_results=10)
        provider = "DDG"
        if not candidates:
            candidates = _search_wikimedia_images(query, max_results=10)
            provider = "Wikimedia"

        if candidates:
            search_results[idx] = candidates
            consecutive_failures = 0
            logger.info(f"[{idx+1}/{total}] {provider} results found for '{query[:50]}'")
        else:
            consecutive_failures += 1
            logger.warning(f"[{idx+1}/{total}] No results for: '{query[:50]}'")

        if progress_cb:
            progress_cb(
                idx + 1, total,
                f"Searched {idx+1}/{total} — {len(search_results)} found"
            )

    if not search_results:
        logger.warning("No search results at all — skipping download phase")
        if progress_cb:
            progress_cb(total, total, "No images found")
        return

    # ── Phase 2: Parallel downloads ──────────────────────────────────
    if progress_cb:
        progress_cb(0, len(search_results), f"Downloading {len(search_results)} images...")

    succeeded = 0
    failed = 0
    done_count = 0
    _counter_lock = threading.Lock()

    def _download_task(idx: int, candidates: List[str]) -> Optional[str]:
        return _download_first_match(candidates, placeholders[idx]["search_query"])

    with ThreadPoolExecutor(max_workers=download_workers) as pool:
        future_to_idx = {
            pool.submit(_download_task, idx, cands): idx
            for idx, cands in search_results.items()
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            el = placeholders[idx]
            try:
                path = future.result()
            except Exception as exc:
                path = None
                logger.warning(f"Download task exception for '{el.get('search_query', '')[:50]}': {exc}")

            with _counter_lock:
                done_count += 1

            if path:
                el["image_local_path"] = path
                el["image_url"] = el.get("image_url") or ""
                succeeded += 1
                logger.info(f"[{done_count}/{len(search_results)}] ✓ '{el['search_query'][:50]}'")
            else:
                failed += 1
                logger.warning(f"[{done_count}/{len(search_results)}] ✗ '{el['search_query'][:50]}'")

            if progress_cb:
                progress_cb(
                    done_count,
                    len(search_results),
                    f"Images: {succeeded} ok, {failed} failed ({done_count}/{len(search_results)})",
                )

    logger.info(
        f"Image pipeline complete: {succeeded} downloaded, {failed} failed, "
        f"{total - len(search_results)} skipped (no DDG results)"
    )
