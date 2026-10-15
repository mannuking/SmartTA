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
RE_LATEX_CMD = re.compile(
    chr(92) + chr(92) + "(?:text|textbf|mathbf|mathrm|textit|operatorname)"
    + chr(92) + chr(92) + chr(123) + "([^}]*)" + chr(92) + chr(92) + chr(125)
)
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

    # Drop accent commands FIRST (before the symbol substitution).
    # Otherwise \hat\delta gets converted to \hatδ (Greek delta) and
    # the accent regex can no longer strip the \hat wrapper.
    BS = chr(92)
    LB = chr(123)  # {
    RB = chr(125)  # }
    accent_names = (
        "hat|bar|tilde|vec|dot|ddot|acute|grave|check|breve"
        "|overline|underline|widehat|widetilde"
    )
    # Pattern A: \hat\{\delta\} (escaped braces) -> \1 = "delta"
    accent_escaped = (
        BS + BS + "(?:" + accent_names + ")"
        + BS + BS + LB + "([^}]*)" + BS + BS + RB
    )
    text = re.sub(accent_escaped, r"\1", text)
    # Pattern B: \hat{\delta} (plain braces) -> \1 = "delta"
    accent_plain_braced = (
        BS + BS + "(?:" + accent_names + ")"
        + BS + LB + "([^}]*)" + BS + RB
    )
    text = re.sub(accent_plain_braced, r"\1", text)
    # Pattern C: \hat\delta (no braces, accent + single-letter cmd)
    accent_plain = (
        BS + BS + "(?:" + accent_names + ")"
        + BS + BS + "([A-Za-z])"
    )
    text = re.sub(accent_plain, r"\1", text)

    # Drop styling prefixes (the content inside the braces is preserved).
    text = RE_LATEX_CMD.sub(r'\1', text)

    # Replace LaTeX commands with Unicode symbols. Sort by length
    # descending so \textbf eats before \text.
    for cmd, symbol in sorted(LATEX_SYMBOL_MAP.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(cmd, symbol)

    # Fractions: \frac{a}{b} -> a/b. Recurse to handle nested.
    while True:
        new_text = RE_FRAC.sub(r'\1/\2', text)
        if new_text == text:
            break
        text = new_text

    # sqrt: \sqrt{x} -> √(x)
    text = RE_SQRT.sub(r'√(\1)', text)

    # Defensive: drop any ceiling/floor command names still left
    BS = chr(92)
    text = text.replace(BS + 'lceil', '⌈').replace(BS + 'rceil', '⌉')
    text = text.replace(BS + 'lfloor', '⌊').replace(BS + 'rfloor', '⌋')

    # Superscripts: x^{ab} -> ^(ab); x^2 -> ^(2)
    text = RE_SUPERSCRIPT.sub(r'^(\1)', text)
    text = re.sub(r'\^([A-Za-z0-9])(?!\{)', r'^(\1)', text)

    # Subscripts: x_{ab} -> _\1 (no parens, like x_(ab) but cleaner);
    # x_i -> _(i). We use _\1 not _(\1) so "q_1" reads as q_1
    # without the parens that look like LaTeX in the output.
    text = RE_SUBSCRIPT.sub(r'_\1', text)
    text = re.sub(r'_([A-Za-z0-9])(?!\{)', r'_(\1)', text)

    # Brace handling:
    #   1. \{ ... \}  -- LaTeX escaped braces, used for SET notation.
    #      Convert to plain { } and keep the content. This is the
    #      critical case: \{q_1, q_2\} must read as {q_1, q_2}
    #      (preserving the set notation, not stripping it).
    text = re.sub(r'\\\{', '{', text)
    text = re.sub(r'\\\}', '}', text)

    #   2. Strip matched outer braces that wrap a single balanced
    #      expression that does NOT contain a comma (e.g. {q_0} -> q_0).
    #      If the content has a comma, it's a SET, so keep the braces.
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
        if depth != 0:
            return s
        inner = s[1:-1]
        if ',' in inner:
            return s  # keep the braces for set notation
        return inner
    text = _strip_matched_outer(text)

    #   3. Strip ALL remaining unmatched braces (leftover from
    #      dropped commands). These are pure LaTeX grouping that
    #      has no meaning in plain text, so just delete them.
    #      (Was previously converting { -> ( and } -> ) which
    #      mangled set notation like \{q_1, q_2\} into
    #      \q_1, q_2\).)
    text = re.sub(r'\{', '', text)
    text = re.sub(r'\}', '', text)

    # Normalize LaTeX whitespace commands and collapse spaces
    text = RE_LATEX_WS.sub(' ', text)
    text = RE_MULTI_SPACE.sub(' ', text)

    # Final cleanup: strip any remaining stray backslashes that
    # don't precede a recognized command. This catches the case
    # where the LLM emits literal "\." (escaped period),
    # "\\q_1" (stray backslash from set notation), or any other
    # leftover LaTeX noise. We keep backslashes that are clearly
    # still part of a command (followed by an ASCII letter), so
    # we don't accidentally mangle math.
    #
    # First, drop duplicate backslashes (\\ -> \) since the LLM
    # sometimes emits LaTeX already-escaped braces.
    while BS + BS in text:
        text = text.replace(BS + BS, BS)
    # Single backslashes followed by punctuation: \, \; \! \. \:
    for esc_punct in [",", ";", "!", ".", ":", "?"]:
        text = text.replace(BS + esc_punct, esc_punct)
    # Trailing lone backslashes
    text = text.rstrip(BS)
    # Standalone backslashes not followed by an ASCII letter. This
    # catches "\q_1" and "\." and similar stray backslashes that
    # survived the LATEX_SYMBOL_MAP / RE_LATEX_CMD passes (i.e.
    # not a known command). We do this AFTER punctuation cleanup
    # so the punctuation cases get handled first.
    LB_NA = chr(92) + chr(92) + "(?![A-Za-z])"
    text = re.sub(LB_NA, "", text)
    # And finally, strip stray backslashes before lowercase letters
    # that are NOT part of a recognized LaTeX command. Since the
    # LATEX_SYMBOL_MAP and RE_LATEX_CMD already converted all known
    # commands, anything that survived is either \delta-style
    # (already converted to Greek) or a stray. Keep a short
    # allowlist of common commands the LLM might have used that
    # we haven't mapped yet.
    _KNOWN_CMD_ALLOWLIST = {
        "to", "gets", "frac", "sqrt", "notin", "in", "subset",
        "supset", "le", "ge", "ne", "approx", "equiv", "sim",
        "to", "rightarrow", "leftarrow", "Leftrightarrow",
        "mapsto", "forall", "exists", "nexists", "wedge", "vee",
        "neg", "bot", "top", "vdash", "dashv", "models",
    }
    def _strip_unknown_bs(m):
        cmd = m.group(1)
        if cmd in _KNOWN_CMD_ALLOWLIST:
            return m.group(0)
        return cmd  # drop the backslash, keep the command name
    text = re.sub(chr(92) + chr(92) + "([A-Za-z]+)", _strip_unknown_bs, text)

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

    # Sanitize: the LLM sometimes emits garbage like
    # "<Kleene Star (R_1)*>" or "<*<i*>>" which ReportLab's HTML
    # parser then mis-interprets as malformed tags and chokes on.
    # We already escaped "<" and ">" above, but the LLM may have
    # already written unescaped HTML tags that, after escape_xml,
    # became "&lt;tag&gt;". Those are safe. The problem case is
    # when the LLM emits "*<i*>" which our italic regex below
    # turns into "<i>>" -- an unbalanced tag. We pre-strip the
    # LLM's *-only emphasis attempts to avoid creating broken
    # tags. Specifically: any "*" not adjacent to a letter/digit
    # is suspicious, so we convert bare "*<" and ">*" sequences
    # to " *<" and ">* " before the italic regex runs.
    # This is intentionally conservative.

    # Convert markdown emphasis in non-code text.
    # IMPORTANT: the input has already been XML-escaped by escape_xml(),
    # so '&' is part of '&lt;' and '&gt;'. The italic regex needs to
    # NOT match '*' characters that are part of escaped HTML entities
    # (e.g. "&lt;Kleene Star (R_1)&gt;" should not get its lone '*'
    # turned into a stray <i></i>). So we require both the opening
    # and closing '*' to be preceded by a non-entity character (i.e.
    # preceded by a letter/digit, not '&' or ';' or '<').
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # Italic: require '*' to be at a word boundary AND the content
    # to start with a non-'&' character (so we don't pick up
    # entities).  This is conservative but safe.
    text = re.sub(
        r'(?:(?<=^)|(?<=[\s\w\d\)\]\}]))\*([^\*\n\&]+?)\*(?=[\s\w\d\)\,\.\;\:\?\!]|$)',
        r'<i>\1</i>',
        text,
    )
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
