"""Render LaTeX math equations to transparent PNG images using matplotlib's
mathtext engine.

ReportLab Paragraphs can't render LaTeX natively. The previous behavior
was to dump the raw LaTeX as italic Times text -- which is why
\\hat{\\delta} appeared as the literal backslash-words in the exported
PDF. Now we render the equation to a transparent PNG and embed that.

Supports the mathtext subset:
  - accents: \\hat x, \\dot x, \\tilde x, \\vec x, \\overline x, etc.
  - greek letters: \\delta, \\sigma, \\Sigma, ...
  - binary ops: \\cup, \\cap, \\times, \\cdot, \\oplus, ...
  - relations: \\leq, \\geq, \\neq, \\approx, \\in, \\subset, ...
  - arrows: \\rightarrow, \\Rightarrow, \\leftrightarrow, ...
  - big ops: \\sum, \\prod, \\int, \\bigcup, \\bigcap
  - fractions: \\frac{a}{b}
  - sqrt: \\sqrt{x}
  - sub/superscripts: x^2, x_i, x_{ij}
  - common delimiters: (, ), [, ], \\{, \\}, |, \\|, \\langle, \\rangle
  - text inside math: \\text{foo}
  - spacing: \\,, \\;, \\:, \\!, \\quad, \\qquad

It does NOT support:
  - \\begin/end environments (matrix, cases, align)
  - non-standard commands the LLM might invent
  - precise font kerning, real LaTeX typesetting

For commands mathtext doesn't know, we fall back to a Unicode-cleaned
string. So \\mathcal{P} becomes 'P' (we drop the calligraphic styling)
or we use a Unicode equivalent when one exists.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

# Limit matplotlib's font-cache complaints
import logging as _logging
_logging.getLogger("matplotlib.font_manager").setLevel(_logging.ERROR)

from .utils import LATEX_SYMBOL_MAP  # reuse the existing symbol map


# ── Equations that mathtext cannot parse -------------------------------------
# These are common LaTeX commands the model produces. We pre-substitute
# them with Unicode equivalents BEFORE handing the string to mathtext.
# Order matters: longer replacements first to avoid partial matches.
_LATEX_TO_UNICODE = {
    # Calligraphic / blackboard / fraktur -- no Unicode for most, drop the
    # styling prefix; the letter stays.
    r"\mathcal": "",
    r"\mathrm": "",
    r"\mathbf": "",
    r"\mathit": "",
    r"\mathfrak": "",
    r"\mathbb": "",
    r"\mathsf": "",
    r"\mathtt": "",
    r"\text": "",
    r"\textbf": "",
    r"\textit": "",
    r"\textrm": "",
    r"\textsf": "",
    r"\texttt": "",
    r"\operatorname": "",
    # Big delimiters (mathtext understands \langle but not these)
    r"\lceil": "\u2308",     # ⌈
    r"\rceil": "\u2309",     # ⌉
    r"\lfloor": "\u230a",    # ⌊
    r"\rfloor": "\u230b",    # ⌋
    # Big operators (mathtext has \sum, \prod, \int, \bigcup, \bigcap)
    r"\bigotimes": "\u2297",
    r"\bigoplus": "\u2295",
    r"\bigvee": "\u22c1",
    r"\bigwedge": "\u22c0",
    # Negations
    r"\notin": "\u2209",
    r"\nsubseteq": "\u2288",
    r"\not\subset": "\u2284",
    r"\not= ": "\u2260",
    # Set ops mathtext doesn't recognize
    r"\setminus": "\\",  # backslash as in L \ M
    r"\backslash": "\\",
    # More arrows
    r"\mapsto": "\u21a6",   # ↦
    r"\hookrightarrow": "\u21aa",
    r"\twoheadrightarrow": "\u21a0",
    # Ellipsis
    r"\ldots": "...",
    r"\cdots": "\u22ef",     # ⋯
    r"\dots": "\u2026",
    # Dots & accents above/below
    r"\dot": "\\dot",
    r"\ddot": "\\ddot",
    r"\acute": "\\acute",
    r"\grave": "\\grave",
    r"\check": "\\check",
    r"\breve": "\\breve",
    r"\bar": "\\bar",
    r"\vec": "\\vec",
    r"\tilde": "\\tilde",
    r"\hat": "\\hat",
    r"\widehat": "\\widehat",
}


# ── Public: render_equation_to_png -------------------------------------------

# Cache: equation text -> file path. Many topics reuse identical
# sub-equations ("q_0", "\\delta", etc.) so this saves a lot of work.
_RENDER_CACHE_DIR: Optional[Path] = None


def _get_cache_dir() -> Path:
    """Per-process cache directory for rendered equations."""
    global _RENDER_CACHE_DIR
    if _RENDER_CACHE_DIR is None or not _RENDER_CACHE_DIR.exists():
        # Use a stable cache dir so the figures survive restarts.
        # The old image_placeholder code used /tmp via tempfile,
        # which got nuked on every export. This one persists.
        cache = Path(tempfile.gettempdir()) / "smartta_equation_cache"
        cache.mkdir(parents=True, exist_ok=True)
        _RENDER_CACHE_DIR = cache
    return _RENDER_CACHE_DIR


def _preprocess_for_mathtext(equation: str) -> str:
    """Convert LaTeX commands the model emits into something mathtext
    can parse. Mostly: drop the styling prefix, keep the content.
    """
    if not equation:
        return ""

    text = equation.strip()
    # Strip display delimiters
    text = text.replace("$$", "")
    text = text.strip("$").strip()
    # Strip trailing/leading whitespace
    text = text.strip()

    # Apply the LATEX_TO_UNICODE table (lengths longest-first to avoid
    # partial collisions like \\text before \\textbf).
    for cmd, repl in sorted(_LATEX_TO_UNICODE.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(cmd, repl)

    # Apply the broader Unicode symbol map (already imported).
    for cmd, sym in LATEX_SYMBOL_MAP.items():
        text = text.replace(cmd, sym)

    # Clean up empty braces left over from dropped commands
    text = re.sub(r"\{\}", "", text)

    # mathtext needs every underscore-prefix or caret-prefix to be either
    # a single char or wrapped in braces. The model sometimes emits q_0
    # and sometimes q_{0}. Normalize simple cases.
    text = re.sub(r"\^([A-Za-z0-9])", r"^{\1}", text)
    text = re.sub(r"_([A-Za-z0-9])(?!\{)", r"_{\1}", text)

    # If the model left a stray {q} in q_{q_0} etc, that's fine for
    # mathtext. Just make sure every open brace has a matching close.
    open_b = text.count("{")
    close_b = text.count("}")
    if open_b > close_b:
        text = text + "}" * (open_b - close_b)
    elif close_b > open_b:
        # Too many closes -- remove the extras
        text = text[: -((close_b - open_b) // 2) - 1] + text[-((close_b - open_b) // 2):]

    # Collapse multiple spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def render_equation_to_png(
    equation: str,
    *,
    fontsize: int = 14,
    dpi: int = 220,
    color: str = "black",
) -> Optional[str]:
    """Render a LaTeX math expression to a transparent PNG on disk.

    Returns the absolute path to the PNG, or None on failure.

    The PNG is cached: identical equation strings return the same file
    path, so re-rendering is essentially free.
    """
    if not equation or not equation.strip():
        return None

    cache = _get_cache_dir()
    # Hash the equation (after preprocessing) for a stable cache key
    cleaned = _preprocess_for_mathtext(equation)
    if not cleaned:
        return None
    key = _hash_for_cache(cleaned + f"|fs{fontsize}|dpi{dpi}|c{color}")
    cached = cache / f"{key}.png"
    if cached.exists() and cached.stat().st_size > 0:
        return str(cached)

    try:
        fig = plt.figure(figsize=(0.01, 0.01))
        # Wrap in $...$ so mathtext treats it as math even if it has
        # plain text mixed in. We use raw r-string so backslashes
        # pass through.
        math_text = f"${cleaned}$"
        # fig.text treats the math_text as the source. If it has plain
        # English words, mathtext will render them upright (not italic)
        # which is exactly what we want for \\text{}.
        fig.text(0, 0, math_text, fontsize=fontsize, color=color)
        # bbox_inches='tight' so the PNG is sized to the content with a
        # tiny pad. transparent=True removes the white background.
        fig.savefig(
            str(cached),
            format="png",
            dpi=dpi,
            bbox_inches="tight",
            pad_inches=0.08,
            transparent=True,
        )
        plt.close(fig)
        if cached.exists() and cached.stat().st_size > 0:
            return str(cached)
        return None
    except Exception as e:
        # If mathtext chokes on this equation, fall back to a plain
        # rendered text version using a regular font. We still write a
        # PNG so the export can include SOMETHING.
        try:
            fig2 = plt.figure(figsize=(0.01, 0.01))
            # Strip ALL backslashes / braces so we just get the symbols
            plain = _strip_latex_to_plain(equation)
            fig2.text(0, 0, plain, fontsize=fontsize, color=color)
            fig2.savefig(
                str(cached),
                format="png",
                dpi=dpi,
                bbox_inches="tight",
                pad_inches=0.08,
                transparent=True,
            )
            plt.close(fig2)
            if cached.exists() and cached.stat().st_size > 0:
                return str(cached)
        except Exception:
            pass
        return None


def _strip_latex_to_plain(equation: str) -> str:
    """Last-resort: strip all LaTeX syntax and return the readable bits."""
    text = equation
    # Apply symbol map
    for cmd, sym in LATEX_SYMBOL_MAP.items():
        text = text.replace(cmd, sym)
    # Drop styling prefixes
    for cmd in _LATEX_TO_UNICODE:
        if text.startswith(cmd):
            text = text[len(cmd):]
    # Remove all braces and backslashes
    text = text.replace("{", "").replace("}", "").replace("\\", "")
    return text


def _hash_for_cache(s: str) -> str:
    import hashlib
    return hashlib.md5(s.encode("utf-8", errors="replace")).hexdigest()[:16]


def get_image_size(path: str) -> Tuple[int, int]:
    """Read PNG dimensions without loading PIL on every call.

    Returns (width_px, height_px). Falls back to (0, 0) if the file is
    missing or unparseable.
    """
    if not os.path.isfile(path):
        return (0, 0)
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return (0, 0)
        # PNG width and height are at bytes 16-23 (big-endian uint32)
        import struct
        w, h = struct.unpack(">II", head[16:24])
        return (w, h)
    except Exception:
        return (0, 0)
