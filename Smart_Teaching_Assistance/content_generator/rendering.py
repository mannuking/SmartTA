"""
Rendering — convert structured note elements to Markdown.
"""

import os
import re
from typing import Dict, List, Optional


def _looks_like_json(text: str) -> bool:
    """Return True if text appears to be raw JSON rather than readable content."""
    stripped = text.strip()
    if stripped.startswith(('{', '[')) and stripped.endswith(('}', ']')):
        return True
    if '"type":' in stripped and '"content":' in stripped:
        return True
    return False


def _sanitize_json_content(text: str) -> str:
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
    return cleaned or text


class RenderingMixin:
    """Markdown rendering for structured lecture notes."""

    @staticmethod
    def _topic_display_label(topic_id: str, title: str) -> str:
        """Build a display label: 'Chapter 1: Title' or '1.1 Title'."""
        num = re.sub(r'^[A-Za-z]+', '', topic_id)
        depth = topic_id.count('.')
        if depth == 0:
            return f"Chapter {num}: {title}"
        return f"{num}  {title}"

    @staticmethod
    def elements_to_markdown(all_notes: List[Dict],
                             answer_key: Optional[str] = None,
                             include_title: bool = True) -> str:
        """Convert structured lecture note elements to a Markdown string."""
        parts: List[str] = []

        for topic_data in all_notes:
            t_id = topic_data['id']
            depth = t_id.count('.')
            hlevel = min(depth + 1, 6)

            # Page separator only before top-level chapters (T1, T2, T3…)
            if include_title and depth == 0 and parts:
                parts.append("\n---\n")

            if include_title:
                label = RenderingMixin._topic_display_label(t_id, topic_data['title'])
                parts.append(f"\n{'#' * hlevel} {label}\n")

            for el in topic_data.get("elements", []):
                t = el.get("type")
                c = el.get("content", "")

                # Sanitize text elements that contain raw JSON
                if t == "text" and _looks_like_json(c):
                    c = _sanitize_json_content(c)

                if t == "heading":
                    level = min(el.get("level", 2) + depth, 6)
                    parts.append(f"{'#' * level} {c}\n\n")
                elif t == "callout":
                    ctype = el.get("callout_type", "Note")
                    parts.append(f"\n> **{ctype}**\n> {c}\n\n")
                elif t == "code":
                    lang = el.get("language", "")
                    parts.append(f"\n```{lang}\n{c}\n```\n\n")
                elif t == "equation":
                    parts.append(f"\n$$\n{c}\n$$\n\n")
                elif t == "figure_block":
                    # Auto-placed figure from the user's uploaded PDF
                    # (set by rag/figure_placer.py during the harvest
                    # step). The path is always an absolute file path
                    # to a PNG on disk; the caption is the same one
                    # the PDF-Extract-Kit pipeline gave the figure.
                    img_src = el.get("image_path", "")
                    caption = el.get("caption", "")
                    page = el.get("page", "")
                    desc = el.get("description", "")
                    if img_src and os.path.isfile(img_src):
                        page_tag = f" (p.{page+1})" if isinstance(page, int) else ""
                        parts.append(
                            f"\n![{caption}]({img_src})\n"
                            f"*Figure: {caption}{page_tag}*\n"
                        )
                        if desc:
                            # Multi-line description collapsed to one paragraph
                            parts.append(f"_{desc}_\n")
                        parts.append("\n")
                    else:
                        parts.append(f"\n> 🖼️ **{caption}** (image file missing)\n\n")
                elif t == "image_placeholder":
                    query = el.get("search_query", "")
                    caption = el.get("caption", "")
                    local_path = el.get("image_local_path", "")
                    url = el.get("image_url", "")
                    img_src = local_path if (local_path and os.path.isfile(local_path)) else url
                    if img_src:
                        parts.append(f"\n![{caption}]({img_src})\n*Figure: {caption}*\n\n")
                    else:
                        parts.append(
                            f"\n> 🖼️ **Image Placeholder**\n"
                            f"> *Search Query:* {query}\n"
                            f"> *Caption:* {caption}\n\n"
                        )
                elif t == "source_citation":
                    parts.append(f"\n> 📚 *Source: {c}*\n\n")
                elif t == "text":
                    parts.append(f"{c}\n\n")
                else:
                    parts.append(f"{c}\n\n")

        if answer_key:
            parts.append("\n---\n# Answer Key for Practice Problems\n")
            parts.append(answer_key)

        return "\n".join(parts)
