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
# All backslashes are written as a raw-string 'r"..."' which keeps the
# source code free of escape conflicts. If you change these patterns,
# keep the leading r on the string literal.
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
    r'\lambda': 'λ', r'\sigma': 'σ', r'\Sigma': 'Σ', r'\Lambda': 'Λ',
    r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ',
    r'\Delta': 'Δ', r'\epsilon': 'ε', r'\varepsilon': 'ε', r'\Epsilon': 'Ε',
    r'\theta': 'θ', r'\pi': 'π', r'\Pi': 'Π',
    r'\phi': 'ϕ', r'\Phi': 'Φ', r'\psi': 'ψ', r'\Psi': 'Ψ',
    r'\omega': 'ω', r'\Omega': 'Ω', r'\Gamma': 'Γ',
    r'\tau': 'τ', r'\rho': 'ρ', r'\mu': 'μ', r'\eta': 'η',
    r'\times': '×', r'\cdot': '·', r'\pm': '±', r'\mp': '∓',
    r'\leq': '≤', r'\le': '≤', r'\geq': '≥', r'\ge': '≥',
    r'\neq': '≠', r'\ne': '≠', r'\approx': '≈', r'\equiv': '≡',
    r'\sim': '~', r'\simeq': '≃', r'\cong': '≅', r'\propto': '∝',
    r'\infty': '∞', r'\partial': '∂', r'\nabla': '∇',
    r'\rightarrow': '→', r'\to': '→',
    r'\leftarrow': '←',
    r'\Rightarrow': '⇒', r'\Leftarrow': '⇐',
    r'\leftrightarrow': '↔',
    r'\uparrow': '↑', r'\downarrow': '↓',
    r'\sum': '∑', r'\prod': '∏', r'\int': '∫', r'\iint': '∬',
    r'\bigcup': '∪', r'\bigcap': '∩', r'\bigoplus': '⊕',
    r'\forall': '∀', r'\exists': '∃',
    r'\in': '∈', r'\notin': '∉',
    r'\subset': '⊂', r'\supset': '⊃',
    r'\subseteq': '⊆', r'\supseteq': '⊇',
    r'\cup': '∪', r'\cap': '∩',
    r'\emptyset': '∅', r'\varnothing': '∅',
    r'\therefore': '∴', r'\because': '∵',
    r'\ldots': '…', r'\cdots': '⋯', r'\dots': '…',
    r'\langle': '⟨', r'\rangle': '⟩',
    r'\neg': '¬', r'\lnot': '¬', r'\land': '∧', r'\wedge': '∧',
    r'\lor': '∨', r'\vee': '∨',
    r'\oplus': '⊕', r'\ominus': '⊖', r'\otimes': '⊗',
    r'\circ': '∘', r'\bullet': '•', r'\star': '⋆',
    r'\mid': '|', r'\nmid': '∤',
    r'\parallel': '∥',
    r'\setminus': '\\',
    r'\lceil': '⌈', r'\rceil': '⌉',
    r'\lfloor': '⌊', r'\rfloor': '⌋',
    r'\triangle': '△', r'\bigtriangleup': '△',
    r'\surd': '√',
}


def clean_latex(text):
    """Convert LaTeX math notation to readable plain text with Unicode symbols.

    The original implementation only handled a narrow subset and missed
    the commands the LLM most often produces (\\hat, \\overline, \\lceil,
    \\bigcup, etc.). The new version:

      1. Replaces the LATEX_SYMBOL_MAP commands with Unicode symbols
      2. Drops the styling prefixes (\\mathcal, \\mathbf, ...) but keeps
         the content
      3. Strips outer braces after substitution
      4. Handles hat/bar/tilde accents by dropping the accent command
         (\\hat{q} -> just q -- the math renderer will use the proper hat)
      5. Cleans up leftover math delimiters
    """
    if not text:
        return text

    # Drop display delimiters
    text = text.replace('$$', '')
    text = RE_DOLLAR.sub('', text)

    # Drop styling prefixes (the content inside the braces is preserved).
    text = RE_LATEX_CMD.sub(r'\1', text)

    # Replace LaTeX commands with Unicode symbols. Sort by length
    # descending so \textbf eats before \text.
    for cmd, symbol in sorted(LATEX_SYMBOL_MAP.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(cmd, symbol)

    # Drop accent commands -- the actual letter inside is preserved;
    # the math renderer (mathtext) handles the visual accent, while
    # this plain-text fallback just keeps the letter.
    text = re.sub(
        r'\\(?:hat|bar|tilde|vec|dot|ddot|acute|grave|check|breve'
        r'|overline|underline|widehat|widetilde)\{([^}]*)\}',
        r'\1', text,
    )

    # Fractions: \frac{a}{b} -> a/b. Recurse to handle nested.
    while True:
        new_text = RE_FRAC.sub(r'\1/\2', text)
        if new_text == text:
            break
        text = new_text

    # sqrt: \sqrt{x} -> √(x)
    text = RE_SQRT.sub(r'√(\1)', text)

    # Defensive: drop any ceiling/floor command names still left
    text = text.replace(r'\lceil', '⌈').replace(r'\rceil', '⌉')
    text = text.replace(r'\lfloor', '⌊').replace(r'\rfloor', '⌋')

    # Superscripts: x^{ab} -> ^(ab); x^2 -> ^(2)
    text = RE_SUPERSCRIPT.sub(r'^(\1)', text)
    text = re.sub(r'\^([A-Za-z0-9])(?!\{)', r'^(\1)', text)

    # Subscripts: x_{ab} -> _(ab); x_i -> _(i)
    text = RE_SUBSCRIPT.sub(r'_\1', text)
    text = re.sub(r'_([A-Za-z0-9])(?!\{)', r'_(\1)', text)

    # Strip outer braces that wrap a single balanced expression
    # (e.g. {q_0} -> q_0). Nested or partial ones are left for later.
    def _strip_matched_outer(s):
        if not (s.startswith('{') and s.endswith('}')):
            return s
        depth = 0
        for i, ch in enumerate(s):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and i < len(s) - 1:
                    return s  # matching close is not at the very end
        return s[1:-1] if depth == 0 else s
    text = _strip_matched_outer(text)

    # Strip ALL remaining unmatched braces (leftover from dropped commands)
    text = re.sub(r'\{', '(', text)
    text = re.sub(r'\}', ')', text)

    # Normalize LaTeX whitespace commands and collapse spaces
    text = RE_LATEX_WS.sub(' ', text)
    text = RE_MULTI_SPACE.sub(' ', text)

    return text.strip()


def clean_markdown(text):
    """Strip markdown artifacts while keeping text readable."""
    if not text: return ""
    text = RE_MD_BOLD_ITALIC.sub('', text)
    text = RE_MD_HEADER.sub('', text)
    text = text.replace('`', '')
    text = RE_MD_HRULE.sub('', text)
    text = RE_MD_LINK.sub(r'\1', text)
    text = clean_latex(text)
    return text.strip()


def md_to_reportlab(text):
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


def escape_xml(text):
    """Escape XML entities for ReportLab Paragraphs."""
    if not text: return ""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def pdf_safe(text):
    """Clean markdown AND escape XML entities for ReportLab Paragraphs."""
    text = clean_markdown(text)
    return escape_xml(text)


def topic_number(topic_id):
    """Convert a topic ID like 'T1', 'T1.2' to '1', '1.2'."""
    return re.sub(r'^[A-Za-z]+', '', topic_id)


def topic_display_label(topic_id, title):
    """Build a display label with numbering."""
    num = topic_number(topic_id)
    depth = topic_id.count('.')
    if depth == 0:
        return f"Chapter {num}: {title}"
    return f"{num}  {title}"


def looks_like_json(text):
    """Return True if text appears to be raw JSON."""
    stripped = text.strip()
    if stripped.startswith(('{', '[')) and stripped.endswith(('}', ']')):
        return True
    if '"type":' in stripped and '"content":' in stripped:
        return True
    return False


def sanitize_json_content(text):
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


def download_image_to_temp(url):
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


def ensure_docx_compatible(img_path):
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


def remove_tags(content):
    """Remove tag markers from content."""
    return re.sub(r'\[(EXAMPLE|ANALOGY|APPLICATION|CASE_STUDY):[^\]]+\]', '', content)
