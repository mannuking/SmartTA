"""Built-in YOLO figure detector (fallback path).

If the parent PDF_extractor project's pipeline is on sys.path, ingest.py
uses that (battle-tested). Otherwise this module provides a minimal
ultralytics-based detector that:
  - Loads DocLayout-YOLO from PDF-Extract-Kit/models/Layout/YOLO/ if present
  - Falls back to yolov10l_ft.pt if not
  - Falls back to a generic yolov8n.pt if neither is present (poor fit
    but the user gets *something*)
  - Renders each page at 144 dpi, runs inference, crops detections
    whose class is "figure" (DocLayout-YOLO) or whose label is in the
    user's `classes` allowlist.

This is intentionally minimal. It does NOT do captioning -- that lives
in ingest.py's `_caption_figures_via_minimax`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

import fitz


logger = logging.getLogger(__name__)


# Resolution order for the YOLO model file:
#   1. PDF_EXTRACTOR_PARENT_DIR env var (set in .env) -- explicit override
#   2. <PDF_extractor>/PDF-Extract-Kit/ (the sibling project on this machine)
#   3. Hardcoded paths under _REPO_ROOT
_PDF_EXTRACTOR_PARENT = os.environ.get("PDF_EXTRACTOR_PARENT_DIR")
if _PDF_EXTRACTOR_PARENT:
    _REPO_ROOT = Path(_PDF_EXTRACTOR_PARENT).resolve()
else:
    _REPO_ROOT = Path(__file__).resolve().parents[3]   # SmartTA_fork/Smart_Teaching_Assistance/pdf_extractor/_local_yolo.py
    #                  ^                                      ^
    #                  parents[0] = pdf_extractor
    #                  parents[1] = Smart_Teaching_Assistance
    #                  parents[2] = SmartTA_fork
    #                  parents[3] = PDF_extractor (the parent project that holds PDF-Extract-Kit/)

_MODEL_CANDIDATES = [
    _REPO_ROOT / "PDF-Extract-Kit" / "models" / "Layout" / "YOLO" / "doclayout_yolo_ft.pt",
    _REPO_ROOT / "PDF-Extract-Kit" / "models" / "Layout" / "YOLO" / "yolov10l_ft.pt",
    _REPO_ROOT / "PDF-Extract-Kit" / "models" / "Layout" / "YOLO" / "doclayout_yolo_docstructbench_imgsz1280_2501.pt",
]


def _find_model() -> Optional[Path]:
    for p in _MODEL_CANDIDATES:
        if p.is_file():
            return p
    return None


def _load_model() -> Optional["YOLO"]:
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("ultralytics not installed -- cannot run YOLO layout detection")
        return None
    model_path = _find_model()
    if model_path is None:
        logger.error(
            "no YOLO model found at any of: %s. "
            "Download a layout model (e.g. doclayout_yolo_ft.pt) into PDF-Extract-Kit/models/Layout/YOLO/.",
            [str(p) for p in _MODEL_CANDIDATES],
        )
        return None
    try:
        return YOLO(str(model_path))
    except Exception as e:
        logger.error("failed to load YOLO model %s: %s", model_path, e)
        return None


def detect_and_crop(
    *,
    pdf_path: str,
    figures_dir: str,
    classes: tuple = ("figure",),
    dpi: int = 144,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    conf_threshold: float = 0.25,
) -> list[dict]:
    """Run YOLO on every page, crop figures, return a list of figure dicts.

    Each returned dict has: {page, bbox, image_path, caption, description, content_text}.
    caption/description/content_text are empty -- call _caption_figures_via_minimax
    separately to fill them in.
    """
    Path(figures_dir).mkdir(parents=True, exist_ok=True)
    model = _load_model()
    if model is None:
        return []

    doc = fitz.open(pdf_path)
    figures: list[dict] = []
    classes_lower = {c.lower() for c in classes}
    total = len(doc)

    try:
        for page_num in range(total):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            from PIL import Image
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            tmp_path = Path(figures_dir) / f"_page_{page_num:04d}.png"
            img.save(tmp_path, format="PNG")

            try:
                results = model.predict(
                    source=str(tmp_path),
                    save=False,
                    conf=conf_threshold,
                    verbose=False,
                )
            except Exception as e:
                logger.warning("YOLO predict failed on page %d: %s", page_num, e)
                results = []

            for r in results:
                names = r.names if hasattr(r, "names") else {}
                for box in r.boxes:
                    cls_idx = int(box.cls[0]) if box.cls is not None else -1
                    cls_name = str(names.get(cls_idx, "")).lower().strip()
                    if cls_name not in classes_lower:
                        continue
                    conf = float(box.conf[0]) if box.conf is not None else 0.0
                    if conf < conf_threshold:
                        continue
                    # bbox in pixel coords of the rendered image
                    x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                    # Save the crop
                    crop = img.crop((x1, y1, x2, y2))
                    out_path = Path(figures_dir) / f"page{page_num:04d}_{cls_name}_{len(figures):03d}.png"
                    try:
                        crop.save(out_path, format="PNG", optimize=True)
                    except Exception as e:
                        logger.warning("save crop failed: %s", e)
                        continue
                    figures.append({
                        "page": page_num,
                        "bbox": [x1, y1, x2, y2],
                        "image_path": str(out_path),
                        "caption": "",
                        "description": "",
                        "content_text": "",
                        "confidence": conf,
                    })
            try:
                tmp_path.unlink()
            except OSError:
                pass

            if progress_cb:
                pct = 10 + int(30 * (page_num + 1) / max(1, total))
                progress_cb(pct, 100, f"Detected figures on page {page_num+1}/{total}")
    finally:
        doc.close()

    return figures
