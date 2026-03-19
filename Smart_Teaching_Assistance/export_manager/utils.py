"""
Shared utilities for ExportManager: regex, cleaning, and sanitization.
"""

import re
import json
import logging
import requests
import os
import tempfile
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# ── Pre-compiled regex patterns ──────────────────────────────────────────────
RE_DOLLAR = re.compile(r'(?<!\\)\$')
RE_LATEX_CMD = re.compile(r'\\(?:text|textbf|mathbf|mathrm|textit|operatorname)\{([^}]*)\}')
RE_FRAC = re.compile(r'\\frac\{([^}]*)\}\{([^}]*)\}')
RE_SUPERSCRIPT = re.compile(r'\^\{([^}]*)\}')
RE_SUBSCRIPT = re.compile(r'_\{([^}]*)\}')
RE_SQRT = re.compile(r'\\sqrt\{([^}]*)\}')
RE_LATEX_WS = re.compile(r'\\(?:quad|qquad|,|;|!|\s)')
RE_MULTI_SPACE = re.compile(r'  +')
RE_MD_BOLD_ITALIC = re.compile(r'[\*\_]{1,3}')
RE_MD_HEADER = re.compile(r'^#+\s*', flags=re.MULTILINE)
RE_MD_HRULE = re.compile(r'^\s*[-*_]{3,}\s*$', flags=re.MULTILINE)
RE_MD_LINK = re.compile(r'\[([^\]]+)\]\([^\)]+\)')

# ── Common LaTeX to Unicode Mapping ──────────────────────────────────────────
LATEX_SYMBOL_MAP = {
    '\\lambda': 'λ', '\\sigma': 'σ', '\\Sigma': 'Σ', '\\Lambda': 'Λ', 
    '\\alpha': 'α', '\\beta': 'β', '\\gamma': 'γ', '\\delta': 'δ',
    '\\Delta': 'Δ', '\\epsilon': 'ε', '\\theta': 'θ', '\\pi': 'π',
    '\\phi': 'ϕ', '\\psi': 'ψ', '\\omega': 'ω', '\\Omega': 'Ω',
    '\\tau': 'τ', '\\rho': 'ρ', '\\mu': 'μ', '\\eta': 'η',
    '\\times': '×', '\\cdot': '·', '\\pm': '±', '\\leq': '≤',
    '\\geq': '≥', '\\neq': '≠', '\\approx': '≈', '\\infty': '∞',
    '\\rightarrow': '→', '\\leftarrow': '←', '\\partial': '∂',
    '\\nabla': '∇', '\\sum': '∑', '\\prod': '∏', '\\int': '∫',
    '\\forall': '∀', '\\exists': '∃', '\\in': '∈', '\\subset': '⊂',
    '\\cup': '∪', '\\cap': '∩', '\\therefore': '∴', '\\because': '∵',
}

def clean_latex(text: str) -> str:
    """Convert LaTeX math notation to readable plain text with Unicode symbols."""
    if not text:
        return text
    
    # Remove math delimiters
    text = text.replace('$$', '')
    text = RE_DOLLAR.sub('', text)
    
    # Handle fractions and commands
    text = RE_LATEX_CMD.sub(r'\1', text)
    text = RE_FRAC.sub(r'\1/\2', text)
    
    # Replace LaTeX commands with Unicode symbols
    for cmd, symbol in LATEX_SYMBOL_MAP.items():
        text = text.replace(cmd, symbol)
    
    # Handle superscripts and subscripts
    text = RE_SUPERSCRIPT.sub(r'^(\1)', text)
    text = RE_SUBSCRIPT.sub(r'_\1', text)
    text = RE_SQRT.sub(r'√(\1)', text)
    
    # Clean up whitespace and braces
    text = RE_LATEX_WS.sub(' ', text)
    text = text.replace('{', '').replace('}', '')
    text = RE_MULTI_SPACE.sub(' ', text)
    
    return text

def clean_markdown(text: str) -> str:
    """Strip markdown artifacts while keeping text readable."""
    if not text: return ""
    text = RE_MD_BOLD_ITALIC.sub('', text)
    text = RE_MD_HEADER.sub('', text)
    text = text.replace('`', '')
    text = RE_MD_HRULE.sub('', text)
    text = RE_MD_LINK.sub(r'\1', text)
    text = clean_latex(text)
    return text.strip()

def md_to_reportlab(text: str) -> str:
    """Convert markdown to ReportLab-friendly XML (<b>, <i>)."""
    if not text:
        return ""

    # First handle LaTeX math if any.
    text = clean_latex(text)

    # Protect inline code spans before running italic/bold regexes.
    # This prevents markers like q_i in code from being parsed as markdown italics.
    code_spans = []

    def _stash_code(match):
        code = match.group(1)
        code_spans.append(code)
        return f"@@CODE_{len(code_spans) - 1}@@"

    text = re.sub(r'`([^`]+?)`', _stash_code, text)

    # Escape XML entities in the remaining non-code text.
    text = escape_xml(text)

    # Convert markdown emphasis in non-code text.
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # Avoid underscores within words (e.g., q_i) by requiring non-word boundaries.
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<i>\1</i>', text)

    # Restore code spans as escaped monospace text.
    for idx, code in enumerate(code_spans):
        safe_code = escape_xml(code)
        text = text.replace(f"@@CODE_{idx}@@", f'<font face="Courier" size="9">{safe_code}</font>')

    return text.strip()

def escape_xml(text: str) -> str:
    """Escape XML entities for ReportLab Paragraphs."""
    if not text: return ""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def pdf_safe(text: str) -> str:
    """Clean markdown AND escape XML entities for ReportLab Paragraphs."""
    text = clean_markdown(text)
    return escape_xml(text)

def topic_number(topic_id: str) -> str:
    """Convert a topic ID like 'T1', 'T1.2' to '1', '1.2'."""
    return re.sub(r'^[A-Za-z]+', '', topic_id)

def topic_display_label(topic_id: str, title: str) -> str:
    """Build a display label with numbering."""
    num = topic_number(topic_id)
    depth = topic_id.count('.')
    if depth == 0:
        return f"Chapter {num}: {title}"
    return f"{num}  {title}"

def looks_like_json(text: str) -> bool:
    """Return True if text appears to be raw JSON."""
    stripped = text.strip()
    if stripped.startswith(('{', '[')) and stripped.endswith(('}', ']')):
        return True
    if '"type":' in stripped and '"content":' in stripped:
        return True
    return False

def sanitize_json_content(text: str) -> str:
    """Extract readable text from content that accidentally contains JSON."""
    pieces = []
    for m in re.finditer(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text):
        piece = m.group(1).replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t')
        if len(piece.strip()) > 10:
            pieces.append(piece.strip())
    if pieces:
        return "\n\n".join(pieces)
    cleaned = re.sub(r'[{}\[\]"]', '', text)
    cleaned = re.sub(r'"?type"?\s*:\s*"?\w+"?,?', '', cleaned)
    cleaned = re.sub(r'"?content"?\s*:', '', cleaned)
    cleaned = re.sub(r'"?level"?\s*:\s*\d+,?', '', cleaned)
    cleaned = re.sub(r'"?elements"?\s*:', '', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return clean_markdown(cleaned) if cleaned else text

def download_image_to_temp(url: str) -> Optional[str]:
    """Download an image from a URL to a temporary file."""
    try:
        response = requests.get(url, timeout=10, stream=True)
        response.raise_for_status()
        fd, temp_path = tempfile.mkstemp(suffix=".jpg")
        with os.fdopen(fd, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return temp_path
    except Exception as e:
        logger.warning(f"Failed to download image from {url}: {e}")
        return None

def ensure_docx_compatible(img_path: str) -> str:
    """Convert unsupported image formats to PNG for python-docx."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(img_path) as im:
            fmt = (im.format or "").upper()
            if fmt in ("GIF", "WEBP", "BMP", "TIFF"):
                png_path = img_path + ".png"
                im.convert("RGBA").save(png_path, "PNG")
                return png_path
    except Exception:
        pass
    return img_path

def remove_tags(content: str) -> str:
    """Remove tag markers from content."""
    return re.sub(r'\[(EXAMPLE|ANALOGY|APPLICATION|CASE_STUDY):[^\]]+\]', '', content)
