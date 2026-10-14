"""PDF ingestion -- the heart of Phase A.

Takes a PDF file path, runs the full extraction pipeline, and returns a
CourseCorpus that downstream RAG and UI steps consume.

Pipeline:

  1. PyMuPDF (fitz) extracts text per page
  2. DocLayout-YOLO (via the parent PDF_extractor project's pipeline)
     detects figure bboxes, ignoring formulas and tables
  3. PyMuPDF crops each figure to a PNG on disk
  4. MiniMax M3 (with Gemini fallback) captions each figure
  5. Section detector builds a chapter/section tree from the PDF TOC
  6. Each chunk and figure is tagged with section_id

The output is a CourseCorpus that the rag/ store.py module indexes
into Chroma for retrieval.

Why the parent PDF_extractor project is preferred:
  - The parent project already has battle-tested YOLO layout detection
    (extraction.py), MiniMax client (MiniMax_client.py), and image-text
    matching (matching.py). Reusing them keeps the surface area small.
  - We fall back to a built-in lightweight extractor if the parent
    project is not on sys.path, so the SmartTA fork can still run
    standalone after `uv sync` (see `pyproject.toml`).

Lazy model loading:
  - DocLayout-YOLO and the MiniMax client are heavy. We import them
    inside the function, not at module top, so importing `ingest`
    doesn't require the user to have a GPU / API key.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

import fitz  # PyMuPDF

from .corpus import CourseCorpus, Figure, TextChunk, SectionTree
from .section_detector import build_section_tree, find_section_for_page


logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────

def hash_pdf(pdf_path: str) -> str:
    """SHA-256 of the PDF bytes. Used as the cache key."""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _is_figure_only(figure_dict: dict) -> bool:
    """Filter a parent's VisualElement dict to only keep figures (drop tables/formulas).

    The parent pipeline tags each element with a 'type' field that's one of
    'figure' / 'table' / 'formula'. The SmartTA spec wants only figures
    because tables and formulas are regenerated as text by the LLM.
    """
    t = str(figure_dict.get("type", "")).lower().strip()
    if t == "figure":
        return True
    # Some parent versions use 'cls' or 'class_name' instead of 'type'
    for k in ("cls", "class_name", "label"):
        v = str(figure_dict.get(k, "")).lower().strip()
        if v == "figure":
            return True
    return False


def _cuda_available() -> bool:
    """Cheap, one-shot check for CUDA. We don't import torch at module top
    to keep the import graph light (the rest of the file doesn't need it)."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _ensure_dir(p: str | Path) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _render_page(page: fitz.Page, dpi: int = 144) -> "PIL.Image.Image":
    """Render a fitz Page to a PIL image at the given dpi."""
    from PIL import Image
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img


def _crop_figure_png(page: fitz.Page, bbox_xyxy, dpi: int, out_path: str) -> bool:
    """Render the page at `dpi` and crop a figure to a PNG. Returns True on success."""
    try:
        img = _render_page(page, dpi=dpi)
        # The bbox is in pixel coords at the same dpi. PyMuPDF gives
        # coordinates in points (1/72 inch). We rendered at `dpi`, so
        # multiply bbox by dpi/72.
        scale = dpi / 72.0
        x1, y1, x2, y2 = [int(v * scale) for v in bbox_xyxy]
        x1 = max(0, x1); y1 = max(0, y1)
        x2 = min(img.width, x2); y2 = min(img.height, y2)
        if x2 - x1 < 16 or y2 - y1 < 16:
            return False  # too small, probably noise
        cropped = img.crop((x1, y1, x2, y2))
        cropped.save(out_path, format="PNG", optimize=True)
        return True
    except Exception as e:
        logger.warning("figure crop failed: %s", e)
        return False


# ── Layered extractor import (parent project preferred) ─────────────

def _try_parent_extractor():
    """Try to import the parent's pipeline.extraction (battle-tested YOLO + captions)."""
    try:
        # The parent project is a sibling of SmartTA_fork. The launcher
        # in the user's run command is expected to add it to sys.path.
        from pipeline.extraction import extract_layout_visuals, caption_visual_elements
        from pipeline.matching import attach_images_to_elements
        return ("parent", extract_layout_visuals, caption_visual_elements, attach_images_to_elements)
    except Exception as e:
        logger.info("parent PDF_extractor pipeline not on sys.path (%s); using built-in extractor", e)
        return None


def _try_local_extractor():
    """Built-in fallback: use ultralytics YOLO directly, with the user's model file."""
    try:
        from ultralytics import YOLO
    except ImportError:
        return None
    return ("local", None, None, None)


# ── Public API ───────────────────────────────────────────────────────

def ingest_pdf(
    pdf_path: str,
    *,
    courses_dir: str | Path,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    force: bool = False,
    device: Optional[str] = None,
) -> CourseCorpus:
    """Run the full extraction pipeline on `pdf_path` and return a CourseCorpus.

    Args:
        pdf_path: Absolute path to the PDF.
        courses_dir: Where to write extracted images + the corpus pickle.
            Typically `Smart_Teaching_Assistance/data/courses`.
        progress_cb: Optional (done, total, message) callback for UI.
        force: Re-extract even if a cached corpus exists.
        device: YOLO device override (`"cuda"`, `"cpu"`, `"mps"`). If None,
            the parent pipeline's default wins.

    Returns:
        CourseCorpus. Cached one is returned if available.
    """
    pdf_path = str(Path(pdf_path).resolve())
    if device is not None:
        os.environ["PDF_EXTRACT_DEVICE"] = device
    pdf_hash = hash_pdf(pdf_path)
    courses_dir = _ensure_dir(courses_dir)
    corpus_dir = _ensure_dir(courses_dir / pdf_hash)
    figures_dir = _ensure_dir(corpus_dir / "figures")

    # ── Cache hit? ────────────────────────────────────────────────
    if not force:
        cached = CourseCorpus.load(courses_dir, pdf_hash)
        if cached is not None:
            logger.info("ingest: cache hit for %s", pdf_hash)
            if progress_cb:
                progress_cb(100, 100, f"Loaded cached corpus ({len(cached.figures)} figures, {len(cached.text_chunks)} chunks)")
            return cached

    started = time.time()
    if progress_cb:
        progress_cb(0, 100, "Opening PDF…")

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    pdf_filename = Path(pdf_path).name
    corpus = CourseCorpus(
        pdf_hash=pdf_hash,
        pdf_path=pdf_path,
        pdf_filename=pdf_filename,
        created_at=time.time(),
        total_pages=total_pages,
    )

    # ── Step 1: section tree from PDF outline ─────────────────────
    if progress_cb:
        progress_cb(5, 100, "Detecting chapter structure…")
    tree_dict = build_section_tree(pdf_path)
    corpus.section_tree = SectionTree.from_dict(tree_dict)
    logger.info("ingest: section tree has %d top-level chapters", len(tree_dict))

    # ── Step 2: layout detection (figures only) ───────────────────
    if progress_cb:
        progress_cb(10, 100, "Detecting figures with YOLO…")

    # Try the parent project first (battle-tested)
    parent = _try_parent_extractor()
    figures_raw: list[dict] = []

    if parent is not None:
        _kind, extract_layout_visuals, caption_visual_elements, _attach = parent
        try:
            # Parent's signature: (pdf_path, pdf_hash, images_dir, device, conf_thres, dpi).
            # No `classes` filter -- it always returns all 3 types (figure/table/formula),
            # and we filter for "figure" downstream because tables and formulas get
            # regenerated as text by the LLM during notes generation.
            # Device priority: PDF_EXTRACT_DEVICE env var > auto-detect (cuda if avail).
            _dev = os.environ.get("PDF_EXTRACT_DEVICE") or ("cuda" if _cuda_available() else "cpu")
            figures_raw = extract_layout_visuals(
                pdf_path=pdf_path,
                pdf_hash=pdf_hash,
                images_dir=str(figures_dir),
                device=_dev,
                dpi=144,
            )
            # Filter to figures only.
            figures_raw = [f for f in figures_raw if _is_figure_only(f)]
            logger.info("ingest: parent extractor returned %d figures after filter", len(figures_raw))
        except Exception as e:
            logger.warning("parent extractor failed: %s; falling back", e)
            figures_raw = []

    if not figures_raw:
        # Local fallback: run YOLO directly with the parent project's
        # configured model file. This is a degraded path -- we don't get
        # captioning or the parent's polish -- but the figures still get
        # cropped so the user can see something.
        from . import _local_yolo
        figures_raw = _local_yolo.detect_and_crop(
            pdf_path=pdf_path,
            figures_dir=str(figures_dir),
            classes=("figure",),
            dpi=144,
            progress_cb=progress_cb,
        )
        # No captioning in fallback -- the descriptions will be empty
        # until the user runs captioning separately. That's OK for v1.
        for fig in figures_raw:
            fig.setdefault("caption", "")
            fig.setdefault("description", "")
            fig.setdefault("content_text", "")

    # ── Step 3: caption each figure (MiniMax -> Gemini) ────────────
    if progress_cb:
        progress_cb(40, 100, f"Captioning {len(figures_raw)} figures…")

    if parent is not None and figures_raw and not figures_raw[0].get("caption"):
        # Parent's caption_visual_elements(catalog, api_key, images_dir) -- in-place
        try:
            _kind, _x, caption_visual_elements, _y = parent
            # The parent's API key is read from env by default. We don't
            # need to pass it; the env has GEMINI_API_KEY configured.
            figures_raw = caption_visual_elements(
                figures_raw,
                images_dir=str(figures_dir),
            )
        except Exception as e:
            logger.warning("parent captioner failed: %s", e)

    # Whether or not the parent captioned, we want a LONGER description
    # (2-3 sentences) for use in detailed notes. The parent only does a
    # 7-word phrase. We run the long-form captioner on figures that
    # don't have a description yet (so we don't double-charge).
    has_long_desc = any(
        len(str(f.get("description") or "").split()) > 12
        for f in figures_raw
    )
    if not has_long_desc and figures_raw:
        figures_raw = _caption_figures_via_minimax(figures_raw, progress_cb=progress_cb)

    # ── Step 4: build Figure objects with section_id + bbox ───────
    if progress_cb:
        progress_cb(70, 100, "Tagging figures with sections…")
    for i, raw in enumerate(figures_raw):
        page = int(raw.get("page", 0))
        bbox = raw.get("bbox", [0, 0, 0, 0])
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            bbox = tuple(bbox)
        else:
            bbox = (0, 0, 0, 0)
        image_path = raw.get("image_path", "")
        if not image_path or not Path(image_path).is_file():
            continue
        section_id = find_section_for_page(tree_dict, page + 1)  # 1-based
        # Parent stores a single 7-word 'description' field. Map it to
        # both our caption and description slots; the placeholder for
        # 'description' will get upgraded later by the rag/figure_placer
        # if we run an extra captioning pass. For v1 the caption IS the
        # description -- they share the same string.
        desc = (raw.get("description") or raw.get("caption") or "").strip()
        cap = (raw.get("caption") or desc).strip()
        corpus.figures.append(Figure(
            id=f"fig-p{page}-c{i}",
            page=page,
            bbox=bbox,
            image_path=str(image_path),
            caption=cap,
            description=desc,
            section_id=section_id,
            content_text=(raw.get("content_text") or "").strip(),
        ))

    # ── Step 5: text extraction + chunking ────────────────────────
    if progress_cb:
        progress_cb(80, 100, "Chunking text by section…")
    full_text_parts: list[str] = []
    chunk_idx = 0
    for page_num in range(total_pages):
        page = doc[page_num]
        page_text = page.get_text("text")
        if not page_text.strip():
            continue
        full_text_parts.append(page_text)
        # Naive chunker: split on double newlines (paragraphs), group
        # up to ~512 tokens. Good enough for v1; we can swap in a
        # semantic chunker later.
        paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()]
        buf = ""
        for p in paragraphs:
            if len(buf) + len(p) > 1800:  # ~512 tokens of English
                if buf:
                    section_id = find_section_for_page(tree_dict, page_num + 1)
                    corpus.text_chunks.append(TextChunk(
                        id=f"chunk-p{page_num}-c{chunk_idx}",
                        page=page_num,
                        section_id=section_id,
                        text=buf.strip(),
                    ))
                    chunk_idx += 1
                buf = p
            else:
                buf = (buf + "\n\n" + p) if buf else p
        if buf.strip():
            section_id = find_section_for_page(tree_dict, page_num + 1)
            corpus.text_chunks.append(TextChunk(
                id=f"chunk-p{page_num}-c{chunk_idx}",
                page=page_num,
                section_id=section_id,
                text=buf.strip(),
            ))
            chunk_idx += 1

    corpus.full_text = "\n\n".join(full_text_parts)
    doc.close()

    # ── Step 6: persist ───────────────────────────────────────────
    if progress_cb:
        progress_cb(95, 100, "Saving corpus…")
    corpus.save(courses_dir)

    elapsed = time.time() - started
    logger.info(
        "ingest: %s -- %d pages, %d figures, %d chunks (%.1fs)",
        pdf_filename, total_pages, len(corpus.figures), len(corpus.text_chunks), elapsed,
    )
    if progress_cb:
        progress_cb(100, 100, f"Done -- {len(corpus.figures)} figures, {len(corpus.text_chunks)} chunks ({elapsed:.1f}s)")
    return corpus


# ── Built-in captioner (minimax_client shim) ─────────────────────────

def _caption_figures_via_minimax(
    figures: list[dict],
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> list[dict]:
    """Add a 2-3 sentence description to each figure using MiniMax M3 vision.

    The parent's caption_visual_elements only writes a 7-word 'description'.
    This function writes a LONGER, paragraph-form 'description' suitable for
    the printed lecture notes. It only fills in figures whose description
    is missing or too short.

    Falls back to Gemini (via the minimax_client shim) on 402/429/5xx.
    """
    try:
        from .. import minimax_client as genai
        from ..config import load_config_from_env
        cfg = load_config_from_env()
        genai.configure(
            api_key=cfg.minimax_api_key,
            base_url=cfg.minimax_base_url,
            model=cfg.gemini_model,
        )
        model = genai.GenerativeModel(cfg.gemini_model)
    except Exception as e:
        logger.warning("could not configure minimax_client for captioning: %s", e)
        for f in figures:
            f.setdefault("caption", "")
            f.setdefault("description", "")
        return figures

    for i, fig in enumerate(figures):
        # Skip if a long description already exists.
        existing = str(fig.get("description") or "").strip()
        if len(existing.split()) > 12:
            continue
        image_path = fig.get("image_path", "")
        if not image_path or not Path(image_path).is_file():
            continue
        try:
            from PIL import Image
            img = Image.open(image_path).convert("RGB")
            prompt = (
                "You are writing a caption for a figure extracted from a "
                "textbook or research paper. The figure will be printed in "
                "lecture notes alongside its caption. Write:\n"
                "  - caption: a single line of MAXIMUM 7 WORDS that names "
                "exactly what the figure shows (e.g. 'DFA state transition table', "
                "'Mitochondria cell diagram'). No leading article ('A', 'The'), "
                "no period, no full sentence.\n"
                "  - description: 2-3 sentences (60-120 words) describing "
                "the figure in detail -- what it shows, what the key elements "
                "are, what relationship it illustrates. Be specific.\n"
                "Respond with ONLY valid JSON, no prose around it."
            )
            resp = model.generate_content(
                [prompt, img],
                generation_config={"temperature": 0.2, "max_output_tokens": 350},
            )
            text = (resp.text or "").strip()
            import json, re as _re
            try:
                # Strip ```json ... ``` if present
                if text.startswith("```"):
                    text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=_re.MULTILINE)
                parsed = json.loads(text)
                new_cap = str(parsed.get("caption", "")).strip()
                new_desc = str(parsed.get("description", "")).strip()
                # If the LLM gave a better caption than the parent, keep it.
                if new_cap and len(new_cap.split()) <= 8:
                    fig["caption"] = new_cap
                if new_desc and len(new_desc.split()) >= 12:
                    fig["description"] = new_desc
            except Exception:
                # Fallback: use the raw text as description.
                if text and len(text.split()) >= 12:
                    fig["description"] = text.strip()
        except Exception as e:
            logger.warning("long-form captioning failed for figure %d: %s", i, e)
        if progress_cb:
            progress_cb(40 + int(30 * (i + 1) / max(1, len(figures))), 100,
                        f"Captioned {i+1}/{len(figures)}")
    return figures


# Pull in re for the JSON strip in _caption_figures_via_minimax
import re  # noqa: E402
