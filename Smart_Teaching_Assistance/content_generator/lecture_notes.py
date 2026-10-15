"""
Stage 3 — Detailed lecture notes generation with narrative flow,
global context, length budgets, sibling dedup, and image population.
"""

import os
import re
import json
import time
import hashlib
import random
import logging
from typing import Dict, List, Tuple, Optional, Callable

from Smart_Teaching_Assistance import PauseRequested

logger = logging.getLogger(__name__)


class LectureNotesMixin:
    """Lecture notes generation, image fetching, context orchestration."""

    @staticmethod
    def _section_tail_context(elements: List[Dict], max_chars: int = 1400) -> str:
        """Build a compact tail context from the most recent generated section."""
        if not elements:
            return ""

        parts: List[str] = []
        # Walk backward and collect meaningful text-bearing elements.
        for el in reversed(elements):
            etype = el.get("type", "")
            content = str(el.get("content", "")).strip()
            if not content:
                continue
            if etype in ("text", "heading", "callout", "equation", "code"):
                parts.append(content)
            if sum(len(p) for p in parts) >= max_chars:
                break

        if not parts:
            return ""

        merged = "\n\n".join(reversed(parts))
        return merged[-max_chars:]

    # ── Prompt Builder ───────────────────────────────────────────────────────
    def _create_lecture_notes_prompt(self, title: str, plan_content: str,
                                    topic_id: str, subject: str,
                                    difficulty: str, highlights: List[str],
                                    parent_context: Optional[Dict] = None,
                                    child_topics: Optional[List[Dict]] = None,
                                    previous_context: Optional[str] = None,
                                    global_context: Optional[str] = None,
                                    length_budget: Optional[Dict] = None,
                                    covered_concepts: Optional[List[str]] = None,
                                    section_mega_prompt: Optional[str] = None,
                                    coverage_contract: Optional[Dict] = None,
                                    book_context: Optional[str] = None) -> str:
        """Build the rich lecture notes generation prompt using Standard Markdown.

        New: `book_context` is an optional RAG-retrieved string of book
        excerpts and figure captions, pulled per topic by the caller (the
        Streamlit step). When present, the LLM is explicitly told to stay
        faithful to it instead of improvising.
        """
        prompt = f"""You are a World-Class Textbook Author writing the definitive guide on {subject}.
Your goal is to write a CHAPTER SECTION that is part of a continuous, cohesive narrative.

Subject: {subject}
Target Audience: {difficulty}
Topic ID: {topic_id}
Topic Title: {title}

Lesson Plan Context:
---
{plan_content}
---
"""
        if book_context:
            prompt += (
                "\n📚 BOOK CONTEXT (RAG-retrieved from the user's uploaded textbook):\n"
                "---\n"
                f"{book_context}\n"
                "---\n"
                "INSTRUCTION: The excerpts above are pulled from the user's textbook. "
                "STAY FAITHFUL to them. Use the same terminology, examples, and "
                "depth of explanation. Cite specific page numbers where helpful "
                "(e.g. 'p.47 of the book'). If the excerpts don't cover a sub-topic, "
                "say so explicitly rather than inventing content.\n"
            )

        if section_mega_prompt:
            prompt += (
                "\nSECTION MEGA PROMPT (teacher-approved guidance; follow this strictly):\n"
                f"{section_mega_prompt}\n"
            )

        if coverage_contract:
            prerequisites = coverage_contract.get("prerequisites", [])
            must_include = coverage_contract.get("must_include", [])
            must_exclude = coverage_contract.get("must_exclude", [])
            bridge_in = coverage_contract.get("bridge_from_previous", "")
            bridge_out = coverage_contract.get("handoff_to_next", "")

            prompt += "\nSECTION COVERAGE CONTRACT (must obey):\n"
            if prerequisites:
                prompt += "Prerequisites to assume (brief recap only):\n"
                for item in prerequisites:
                    prompt += f"  - {item}\n"
            if must_include:
                prompt += "Must include in this section:\n"
                for item in must_include:
                    prompt += f"  - {item}\n"
            if must_exclude:
                prompt += "Must exclude from this section (avoid redundancy):\n"
                for item in must_exclude:
                    prompt += f"  - {item}\n"
            if bridge_in:
                prompt += f"Bridge from previous section: {bridge_in}\n"
            if bridge_out:
                prompt += f"Handoff to next section: {bridge_out}\n"

        if global_context:
            prompt += f"\n{global_context}\n"

        if previous_context:
            prompt += f"\n{previous_context}\n"
        else:
            prompt += "\nThis is the very first section. Start with a strong hook.\n"

        if parent_context:
            prompt += "\nHierarchical Context (Where we are in the book):\n"
            for pid, pdesc in parent_context.items():
                prompt += f"  - {pid}: {pdesc}\n"

        if covered_concepts:
            prompt += (
                "\nALREADY COVERED (do NOT re-define or re-explain these — "
                "reference briefly if needed, but do not teach them again):\n"
            )
            for concept in covered_concepts[-30:]:
                prompt += f"  - {concept}\n"

        if highlights:
            is_highlighted = any(h in topic_id for h in highlights)
            if is_highlighted:
                prompt += f"\n⭐ HIGH PRIORITY: This topic was specifically marked for emphasis. Provide extra detail, more examples, and deeper explanations.\n"
            prompt += f"Emphasize these areas: {', '.join(highlights)}\n"

        if child_topics:
            prompt += f"""
ROLE: CONNECTIVE TISSUE & MOTIVATION (Parent Node)
You are writing the *Introduction* for a major section.
Your job is to "Sell the Chapter" and provide the mental map.
- **ABSOLUTELY PROHIBITED**: Do NOT define, explain, or teach the specific concepts listed in the sub-topics below.
- **DO**: Explain the *historical context* or the *problem statement* that led to these concepts.
- **DO**: Explain the "Architecture" of this domain. How do the sub-topics relate?
- **DO**: Use an analogy to explain the *relationship* between the components.
- **Length**: Keep this section concise (2-3 paragraphs max).

Sub-topics that will follow (AVOID COVERING THESE SPECIFICS):
"""
            for child in child_topics:
                ctitle = child.get("title", "Untitled")
                struct = child.get("structured_content", {})
                scope_txt = ""
                if struct.get("learning_objectives"):
                    scope_txt += f"  - Objectives: {struct['learning_objectives'][:200]}..."
                if struct.get("key_concepts"):
                    scope_txt += f"  - Concepts: {struct['key_concepts'][:200]}..."
                prompt += f"- **{ctitle}**\n{scope_txt}\n"
        else:
            prompt += f"""
ROLE: CORE TEACHING (Leaf Node)
You are the primary teacher for topic '{title}'.
- This is where the actual learning happens.
- Assume the student has read the high-level intro but knows nothing of the details.
- Define terms, provide formulas, walk through examples.
- Be exhaustive and detailed.
"""

        if length_budget:
            length_instruction = f"**Target Length**: {length_budget['label']}. Stay within {length_budget['min_words']}-{length_budget['max_words']} words. Do NOT exceed this."
        else:
            length_instruction = "**Target Length**: Approximately 800-1000 words of high-quality, dense content."

        prompt += f"""
STYLISTIC INSTRUCTIONS:
1.  **Professional & Structured**: Write like a high-quality, industry-standard textbook or technical documentation.
2.  **Clear Hierarchy**: Use headings, subheadings, bullet points, and bold text to organize information logically.
3.  **Concise & Dense**: Zero fluff. Every sentence must communicate a new fact or insight. Use lists and tables where appropriate to condense information.
    4.  {length_instruction}
    5.  **Anti-Redundancy**: Do not repeat full explanations already taught. Reference earlier topics briefly and only expand with new value.
    6.  **Depth First, Not Verbosity**: Include complete conceptual coverage, worked examples, and pitfalls with no filler.
7.  **No Meta-Transitions**: Do NOT write phrases like "in the previous section", "as discussed earlier", or similar meta references. Continue naturally as one continuous book.

OUTPUT FORMAT:
You MUST output ONLY valid JSON. Do not include markdown code blocks (like ```json).
The JSON must follow this exact structure:
{{
  "elements": [
    {{
      "type": "heading",
      "level": 2,
      "content": "Heading text"
    }},
    {{
      "type": "text",
      "content": "Markdown formatted text (paragraphs, lists, bold, etc.)"
    }},
    {{
      "type": "callout",
      "callout_type": "Key Takeaway | Analogy | Practice Problem | Check Your Understanding",
      "content": "Callout content"
    }},
    {{
      "type": "equation",
      "content": "E=mc^2"
    }},
    {{
      "type": "code",
      "language": "python",
      "content": "print('hello')"
    }},
    {{
      "type": "image_placeholder",
      "search_query": "visual description of what image should show to help the student",
      "caption": "Caption for the image"
    }},
    {{
      "type": "source_citation",
      "content": "Reference to the source material or page number"
    }}
  ]
}}

Create the content now:
"""
        return prompt

    # ── Single chunk generation ──────────────────────────────────────────────
    def generate_lecture_notes_chunk(self, lesson_plan: Dict, topic_id: str,
                                    subject: str, difficulty: str,
                                    highlights: List[str],
                                    parent_context: Optional[Dict] = None,
                                    child_topics: Optional[List[Dict]] = None,
                                    temperature: float = 0.7,
                                    previous_context: Optional[str] = None,
                                    global_context: Optional[str] = None,
                                    length_budget: Optional[Dict] = None,
                                    covered_concepts: Optional[List[str]] = None,
                                    book_context: Optional[str] = None) -> List[Dict]:
        """Generate lecture notes for a single topic node.

        `book_context` is the per-topic RAG-retrieved string from the
        uploaded textbook (see Smart_Teaching_Assistance.rag.retriever).
        """
        entry = self._find_lesson_plan_entry(lesson_plan, topic_id)
        if not entry:
            logger.error(f"Could not find lesson plan entry for {topic_id}")
            return []

        section_mega_prompt = entry.get("teaching_prompt", "")
        coverage_contract = entry.get("coverage_contract", {})

        prompt = self._create_lecture_notes_prompt(
            entry["title"], entry.get("content", ""), topic_id,
            subject, difficulty, highlights, parent_context, child_topics,
            previous_context=previous_context,
            global_context=global_context,
            length_budget=length_budget,
            covered_concepts=covered_concepts,
            section_mega_prompt=section_mega_prompt,
            coverage_contract=coverage_contract,
            book_context=book_context,
        )
        try:
            raw = self._generate_text(prompt, temperature, f"notes_{topic_id}")
        except Exception as e:
            logger.error(f"AI generation failed for {topic_id}: {e}")
            return [{"type": "text", "content": f"(Content generation failed for this topic. Error: {e})"}]
        if not raw.strip():
            return []

        elements = self._parse_elements_json(raw, topic_id)
        elements = self._deduplicate_elements(elements)
        return elements

    @staticmethod
    def _normalize_text_for_dedup(text: str) -> str:
        """Normalize text content for near-duplicate detection."""
        if not text:
            return ""
        text = re.sub(r'\s+', ' ', text).strip().lower()
        text = re.sub(r'[^a-z0-9\s]', '', text)
        return text

    def _deduplicate_elements(self, elements: List[Dict]) -> List[Dict]:
        """Drop repeated long text/callout elements to reduce redundancy."""
        seen = set()
        deduped: List[Dict] = []

        for el in elements:
            etype = el.get("type", "")
            content = el.get("content", "")

            if etype in ("text", "callout") and isinstance(content, str):
                norm = self._normalize_text_for_dedup(content)
                # Use prefix signatures to catch repeated paragraphs with tiny variations.
                sig = (etype, norm[:240])
                if len(norm) > 120 and sig in seen:
                    continue
                seen.add(sig)

            deduped.append(el)

        return deduped

    # ── Robust JSON parser for LLM output ────────────────────────────────────
    def _parse_elements_json(self, raw: str, topic_id: str) -> List[Dict]:
        """Parse LLM output into elements list with multiple recovery strategies.

        The parser walks 9 strategies in order. Each one returns either a
        valid `elements` list or None. We return the first success.

        Old behavior failed on Gemini responses that wrapped the JSON
        inside prose like "Here is the structured output:\n```json\n{...}\n```\n
        Let me know if you need more."  -- the outermost {..} would
        include the trailing markdown fence, so json.loads threw. The
        new strategies try balanced-brace extraction and markdown
        fence stripping before giving up.
        """
        if not raw or not raw.strip():
            return []

        cleaned = raw.strip()
        # Strip ALL markdown code fences, not just outer ones. The
        # model sometimes emits ```json on the first line and ``` on
        # the last, but with explanatory prose before/after.
        cleaned = re.sub(r"```(?:json|JSON)?\s*", "", cleaned)
        cleaned = re.sub(r"```\s*", "", cleaned).strip()

        # Strategy 1: direct parse
        elements = self._try_parse_elements(cleaned, topic_id, "direct")
        if elements is not None:
            return elements

        # Strategy 2: find the outermost { ... } and parse
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            subset = cleaned[first_brace:last_brace + 1]
            elements = self._try_parse_elements(subset, topic_id, "brace-trim")
            if elements is not None:
                return elements

        # Strategy 3: find the outermost [ ... ] (bare array)
        first_bracket = cleaned.find("[")
        last_bracket = cleaned.rfind("]")
        if first_bracket >= 0 and last_bracket > first_bracket:
            subset = cleaned[first_bracket:last_bracket + 1]
            try:
                arr = json.loads(subset)
                if isinstance(arr, list):
                    logger.warning(f"Recovered bare array for {topic_id}")
                    return arr
            except json.JSONDecodeError:
                pass

        # Strategy 4: fix common LLM JSON issues (trailing commas, unescaped newlines)
        fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)  # trailing commas
        elements = self._try_parse_elements(fixed, topic_id, "trailing-comma-fix")
        if elements is not None:
            return elements

        # Strategy 5: find the LAST balanced top-level {...} block.
        # The model often adds prose before AND after the JSON, e.g.:
        #   "Sure! Here is the JSON:\n{\"elements\":[...]} \nLet me know
        #   if you need more."  The outer find() picks up the trailing
        #   text, but the *last* balanced top-level object is the real
        #   one. Walk back from the end, tracking brace depth, ignoring
        #   characters inside strings.
        elements = self._try_extract_last_balanced_object(cleaned, topic_id)
        if elements is not None:
            return elements

        # Strategy 6: fix newlines inside string values. The model
        # sometimes embeds literal "\n" inside JSON strings instead of
        # the escape "\\n" -- which is invalid JSON. Replace literal
        # newlines that sit inside quoted content with the escape.
        elements = self._try_parse_elements(
            self._fix_unescaped_newlines_in_strings(cleaned),
            topic_id, "unescaped-newline-fix",
        )
        if elements is not None:
            return elements

        # Strategy 7: try balanced-brace scanning from the START.
        # Walk forward tracking brace depth; the first time depth
        # returns to 0 we have a candidate object. If that fails,
        # continue to the next closing brace and try again.
        elements = self._try_balanced_object_scan(cleaned, topic_id)
        if elements is not None:
            return elements

        # Strategy 8: try the FIRST balanced {...} block, not the
        # outermost. Sometimes the model emits an example template
        # FIRST, then the real answer. The first balanced object is
        # usually the real one.
        elements = self._try_extract_first_balanced_object(cleaned, topic_id)
        if elements is not None:
            return elements

        # Strategy 9: try the regex extraction of just the elements
        # array. Pull out "elements": [ ... ] wherever it appears, even
        # if the wrapping object is broken.
        elements = self._try_extract_elements_array_only(cleaned, topic_id)
        if elements is not None:
            return elements

        # Strategy 10: try to REPAIR a truncated JSON by closing the
        # unbalanced braces/brackets and stripping a dangling trailing
        # comma. The LLM hit max_tokens mid-element; the JSON up to
        # that point is usually valid, just incomplete. By appending
        # the missing closing characters, json.loads succeeds and we
        # get a (truncated) but parseable structure.
        elements = self._try_close_truncated_json(cleaned, topic_id)
        if elements is not None:
            return elements

        # Strategy 11: last-ditch "everything looks like elements".
        # If the raw text has a `elements` key followed by an
        # unbalanced `[`, build a synthetic structure that lists every
        # `type`/`content` pair found in the rest of the text.
        # Cheaper than dropping the whole topic to plain text.
        elements = self._try_fragments_as_elements(cleaned, topic_id)
        if elements is not None:
            return elements

        # All strategies failed -- convert raw text to clean readable elements
        logger.error(f"All JSON recovery failed for {topic_id}, converting to text")
        return self._raw_text_to_elements(raw)

    @staticmethod
    def _is_brace_balanced_at(s: str, end_idx: int) -> bool:
        """Check if a substring s[:end_idx+1] has balanced braces outside strings.

        Used to verify candidate substrings from balanced scans.
        """
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(s):
            if i > end_idx:
                break
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and i == end_idx:
                    return True
        return depth == 0 and end_idx == len(s) - 1 or False

    def _try_extract_last_balanced_object(self, text: str, topic_id: str):
        """Find the LAST top-level balanced {...} block in text and parse it."""
        depth = 0
        in_str = False
        esc = False
        candidates = []  # list of (start, end) substrings where depth returns to 0
        start = -1
        for i, ch in enumerate(text):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append((start, i + 1))
                    start = -1
        if not candidates:
            return None
        for s, e in reversed(candidates):
            sub = text[s:e]
            elems = self._try_parse_elements(sub, topic_id, "last-balanced")
            if elems is not None:
                return elems
        return None

    def _try_extract_first_balanced_object(self, text: str, topic_id: str):
        """Find the FIRST top-level balanced {...} block and parse it."""
        depth = 0
        in_str = False
        esc = False
        start = -1
        for i, ch in enumerate(text):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                if depth == 0 and start >= 0:
                    sub = text[start:i + 1]
                    elems = self._try_parse_elements(sub, topic_id, "first-balanced")
                    if elems is not None:
                        return elems
                    start = -1
        return None

    def _try_balanced_object_scan(self, text: str, topic_id: str):
        """Walk all balanced objects, return the first that parses."""
        depth = 0
        in_str = False
        esc = False
        start = -1
        for i, ch in enumerate(text):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                if depth == 0 and start >= 0:
                    sub = text[start:i + 1]
                    elems = self._try_parse_elements(sub, topic_id, "balanced-scan")
                    if elems is not None:
                        return elems
                    start = -1
        return None

    def _try_extract_elements_array_only(self, text: str, topic_id: str):
        """Try to find a "elements": [ ... ] block, even if the wrapping
        object is broken. Useful when the LLM truncates mid-JSON.
        """
        # Find every "elements": [ ... ] and try each
        idx = 0
        while True:
            key_pos = text.find('"elements"', idx)
            if key_pos == -1:
                break
            # Find the opening [ after this key
            bracket_pos = text.find("[", key_pos)
            if bracket_pos == -1:
                idx = key_pos + 1
                continue
            # Walk to matching ]
            depth = 0
            in_str = False
            esc = False
            end_pos = -1
            for j in range(bracket_pos, len(text)):
                ch = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        end_pos = j + 1
                        break
            if end_pos > 0:
                sub = text[bracket_pos:end_pos]
                try:
                    arr = json.loads(sub)
                    if isinstance(arr, list):
                        logger.warning(f"Recovered bare elements array for {topic_id}")
                        return arr
                except json.JSONDecodeError:
                    # Try trailing-comma fix
                    sub2 = re.sub(r",\s*([}\]])", r"\1", sub)
                    try:
                        arr = json.loads(sub2)
                        if isinstance(arr, list):
                            return arr
                    except json.JSONDecodeError:
                        pass
            idx = key_pos + 1
        return None

    @staticmethod
    def _fix_unescaped_newlines_in_strings(text: str) -> str:
        """Replace literal newlines inside JSON string values with the
        escape sequence (backslash-n). Only modifies text inside quoted
        strings. Useful for Gemini responses that emit raw newlines in
        string content instead of escaping them.
        """
        # Use chr() to avoid having literal escape characters in the
        # source which can confuse the patch tool / editors.
        NL = chr(10)   # \n
        CR = chr(13)   # 
        TAB = chr(9)   # \t
        BS = chr(92)   # \

        out = []
        in_str = False
        esc = False
        for ch in text:
            if in_str:
                if esc:
                    esc = False
                    out.append(ch)
                elif ch == BS:
                    esc = True
                    out.append(ch)
                elif ch == '"':
                    in_str = False
                    out.append(ch)
                elif ch == NL:
                    out.append(BS + "n")
                elif ch == CR:
                    out.append(BS + "r")
                elif ch == TAB:
                    out.append(BS + "t")
                else:
                    out.append(ch)
            else:
                if ch == '"':
                    in_str = True
                out.append(ch)
        return "".join(out)

    @staticmethod
    def _try_parse_elements(text: str, topic_id: str, strategy: str) -> Optional[List[Dict]]:
        """Attempt to parse text as JSON and extract 'elements' list."""
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "elements" in data:
                return data["elements"]
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    def _try_close_truncated_json(self, text: str, topic_id: str):
        """Repair a truncated JSON by appending missing closing chars.

        The LLM hit max_tokens mid-element. The JSON up to that point
        is usually valid -- just missing the closing } or ] of an
        inner object/array. By counting brace depth (ignoring strings
        and escapes) and appending the missing closers, json.loads
        can succeed and we recover a (truncated) but parseable
        structure.

        Also handles the harder case where the JSON was cut mid-string
        content (e.g. "Last paragraph that got cut). We try multiple
        repair strategies in order:
          1. Just append the missing closers
          2. Close the dangling string, strip a trailing partial
             word, then append the closers
          3. Close the dangling string (without stripping), then
             append the closers (string content may be incomplete but
             json.loads tolerates this when the structure is balanced)

        Returns the elements list, or None if all repair attempts fail.
        """
        s = text.rstrip()
        if not s or not s.startswith(("{", "[")):
            return None
        # Strip a trailing comma if present (illegal in JSON)
        while s.endswith(","):
            s = s[:-1].rstrip()
        # Count braces and brackets, ignoring those inside strings.
        depth_b = 0
        depth_k = 0
        in_str = False
        esc = False
        for ch in s:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth_b += 1
            elif ch == "}":
                if depth_b > 0:
                    depth_b -= 1
            elif ch == "[":
                depth_k += 1
            elif ch == "]":
                if depth_k > 0:
                    depth_k -= 1
        # If we're already balanced, nothing to repair.
        if depth_b == 0 and depth_k == 0 and not in_str:
            return None

        def _try_load(candidate: str):
            """Try a list of plausible close patterns and return the first parse.

            The LLM almost always truncates mid-value-of-`"content"`, so
            the common close is "}]} (close string, close element obj,
            close elements array, close outer object). We try that
            first, then several fallbacks for less-common truncation
            points (inside an array, between fields, inside the outer
            object directly, etc.).
            """
            close_patterns = [
                '"}]}',         # truncate inside element.content (most common)
                '"]}]}',        # same, with different position
                '"]}"]}',       # truncate between fields
                '"]}',          # truncate inside an element with no array
                '"]}"}]',       # truncate inside string within nested object
                '"]}}}',        # more closing braces than needed (extra })
                '"]}}]}',       # even more
                '"]',           # truncate inside a bare string array
                '"}',           # truncate inside a bare string field
            ]
            for extra in close_patterns:
                try:
                    return json.loads(candidate + extra)
                except json.JSONDecodeError:
                    continue
            return None

        # Attempt 1: just append closers
        data = _try_load(s)
        if data is not None:
            logger.warning(
                "Repaired truncated JSON for %s (appended %d braces, %d brackets)",
                topic_id, depth_b, depth_k,
            )
            return self._extract_elements_from_data(data)

        # Attempt 2: if we were in a string, back up to the last safe
        # word boundary (a space), close the string, then append
        # closers. This handles "Last paragraph that got cut by
        # removing "cut" so it becomes "Last paragraph that got " -- a
        # complete, parseable string.
        if in_str:
            # Walk back to the last whitespace in the (incomplete) string
            cut = s.rstrip()
            # Don't strip past the opening quote
            last_quote = cut.rfind('"')
            if last_quote > 0 and last_quote < len(cut) - 1:
                after_quote = cut[last_quote + 1:]
                # Find last space in after_quote
                last_space = after_quote.rfind(' ')
                if last_space > 0:
                    s2 = cut[:last_quote + 1 + last_space]
                    data = _try_load(s2)
                    if data is not None:
                        logger.warning(
                            "Repaired truncated JSON for %s (backed up to last word boundary)",
                            topic_id,
                        )
                        return self._extract_elements_from_data(data)

        return None

    @staticmethod
    def _extract_elements_from_data(data):
        """Pull 'elements' out of a parsed JSON object or list."""
        if isinstance(data, dict) and "elements" in data:
            return data["elements"]
        if isinstance(data, list):
            return data
        return None

    def _try_fragments_as_elements(self, text: str, topic_id: str):
        """Last-ditch: extract every "type"/"content" pair as a text element.

        When the LLM output is severely broken (truncated mid-string,
        or wrapped in a way none of the previous strategies can
        parse), this still salvages the readable content by looking
        for individual element-like fragments anywhere in the text.
        """
        elements = []
        # Look for the "elements" key. If we find it but the rest of
        # the JSON is broken, this is still our best bet.
        for m in re.finditer(
            r'"type"\s*:\s*"([^"]+)"\s*,\s*"content"\s*:\s*"((?:[^"\\]|\\.)*)"',
            text,
        ):
            etype = m.group(1)
            content = m.group(2)
            # Unescape JSON string escapes
            content = content.replace("\\n", "\n").replace('\\"', '"').replace("\\t", "\t")
            if len(content.strip()) > 5:
                el = {"type": etype, "content": content.strip()}
                # Add level for headings so they render right
                if etype == "heading":
                    el["level"] = 2
                elements.append(el)
        # Also try the more lenient {type, content} pattern (no quotes)
        if not elements:
            for m in re.finditer(
                r'type[:=]\s*[\'"]?(\w+)[\'"]?[^}\]]*?content[:=]\s*[\'"]([^\'"}]{5,2000})[\'"]',
                text,
            ):
                etype = m.group(1)
                content = m.group(2)
                if etype in ("text", "heading", "callout", "equation", "code"):
                    el = {"type": etype, "content": content.strip()}
                    if etype == "heading":
                        el["level"] = 2
                    elements.append(el)
        if elements:
            logger.warning(
                "Salvaged %d element fragments for %s (full JSON was unrecoverable)",
                len(elements), topic_id,
            )
            return elements
        return None

    @staticmethod
    def _raw_text_to_elements(raw: str) -> List[Dict]:
        """Convert raw LLM text (possibly containing JSON fragments) to clean text elements.

        Strips JSON structural characters so the user never sees raw JSON.
        """
        # Try to extract readable content values from the JSON-like text
        content_pieces = []
        for m in re.finditer(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', raw):
            piece = m.group(1)
            # Unescape JSON string escapes
            piece = piece.replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t')
            if len(piece.strip()) > 10:  # skip tiny fragments
                content_pieces.append(piece.strip())

        if content_pieces:
            return [{"type": "text", "content": piece} for piece in content_pieces]

        # Last resort: strip all JSON syntax and return whatever readable text remains
        cleaned = re.sub(r'[{}\[\]"]', '', raw)
        cleaned = re.sub(r'"?type"?\s*:\s*"?\w+"?,?', '', cleaned)
        cleaned = re.sub(r'"?content"?\s*:', '', cleaned)
        cleaned = re.sub(r'"?level"?\s*:\s*\d+,?', '', cleaned)
        cleaned = re.sub(r'"?callout_?type"?\s*:\s*"?[^"]*"?,?', '', cleaned)
        cleaned = re.sub(r'"?elements"?\s*:', '', cleaned)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        if cleaned:
            return [{"type": "text", "content": cleaned}]
        return [{"type": "text", "content": "(Content generation produced unreadable output. Please regenerate this topic.)"}]

    # ── Recursive full-course generation ─────────────────────────────────────
    def generate_detailed_notes_recursive(
        self,
        lesson_plan: Dict,
        subject: str,
        difficulty: str,
        highlights: List[str],
        temperature: float = 0.7,
        progress_cb: Optional[Callable] = None,
        existing_notes: Optional[List[Dict]] = None,
        roadmap_tree: Optional[Dict] = None,
        partial_update_cb: Optional[Callable] = None,
        book_context_fn: Optional[Callable[[str, str], str]] = None,
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        Recursively generate detailed lecture notes with continuous narrative flow,
        global context awareness, length budgets, and sibling deduplication.

        Returns:
            Tuple of (all_topic_structured_notes, answer_key_text)
        """
        all_notes: List[Dict] = []
        all_practice: List[Dict] = []
        processed_ids: set = set()
        completed_ids: List[str] = []

        existing_map = {n["id"]: n for n in (existing_notes or [])}

        total = self._count_tree_items(lesson_plan.get("topics", []))
        counter = [0]
        flow_state = {"last_snippet": None, "last_title": None}
        covered_concepts: List[str] = []
        _covered_set: set = set()  # dedup for covered_concepts
        _roadmap_tree = roadmap_tree or lesson_plan

        def _extract_key_concepts(elements: List[Dict]) -> List[str]:
            concepts = []
            for el in elements:
                content = el.get("content", "")
                for match in re.finditer(r'\*\*([^*]{2,50})\*\*', content):
                    concepts.append(match.group(1).strip())
                if el.get("type") == "callout" and "Takeaway" in el.get("callout_type", ""):
                    concepts.append(content[:100].strip())
            return concepts

        def _generate_for_list(data_list, parent_ctx=None):
            nonlocal covered_concepts
            for item in data_list:
                topic_id = item["id"]
                if topic_id in processed_ids:
                    continue
                processed_ids.add(topic_id)

                counter[0] += 1
                if progress_cb:
                    progress_cb(counter[0], total, f"Notes: {topic_id} — {item.get('title', '')}")

                child_topics = []
                for key in ("subtopics", "subsubtopics", "subsubsubtopics"):
                    if key in item and isinstance(item[key], list) and item[key]:
                        child_topics.extend(item[key])

                prev_ctx_str = None
                if flow_state["last_snippet"] and flow_state["last_title"]:
                    prev_ctx_str = self._build_transition_context(
                        flow_state["last_title"],
                        flow_state["last_snippet"],
                        item.get("title", "Untitled")
                    )

                global_ctx = self._build_global_context(
                    _roadmap_tree, topic_id, completed_ids
                )

                depth = topic_id.count(".")
                has_children = bool(child_topics)
                is_highlighted = any(h in topic_id for h in highlights)
                length_budget = self._estimate_target_length(depth, has_children, is_highlighted)

                if topic_id in existing_map:
                    logger.info(f"Resuming: Skipping generation for notes ID {topic_id}")
                    elements = existing_map[topic_id]["elements"]
                    if progress_cb:
                        progress_cb(counter[0], total, f"Loaded: {topic_id} — {item.get('title', '')}")
                else:
                    # Per-topic RAG lookup. The callback returns a string
                    # (RAG-retrieved book context) or empty if the user
                    # didn't upload a PDF. Cheap because the index is in
                    # memory; the call is <100ms per topic.
                    book_ctx = ""
                    if book_context_fn is not None:
                        try:
                            book_ctx = book_context_fn(topic_id, item.get("title", "")) or ""
                        except Exception as e:
                            logger.debug(f"book_context_fn failed for {topic_id}: {e}")
                            book_ctx = ""
                    elements = self.generate_lecture_notes_chunk(
                        lesson_plan, topic_id, subject, difficulty,
                        highlights, parent_ctx, child_topics, temperature,
                        previous_context=prev_ctx_str,
                        global_context=global_ctx,
                        length_budget=length_budget,
                        covered_concepts=covered_concepts if covered_concepts else None,
                        book_context=book_ctx,
                    )

                if elements:
                    all_notes.append({
                        "id": topic_id,
                        "title": item.get("title", "Untitled"),
                        "elements": elements,
                    })
                    completed_ids.append(topic_id)

                    tail_ctx = self._section_tail_context(elements, max_chars=1400)
                    if tail_ctx:
                        flow_state["last_snippet"] = tail_ctx
                        flow_state["last_title"] = item.get("title", "Untitled")

                    new_concepts = _extract_key_concepts(elements)
                    for c in new_concepts:
                        if c not in _covered_set:
                            _covered_set.add(c)
                            covered_concepts.append(c)
                    # Keep only most recent 60 to bound prompt size
                    if len(covered_concepts) > 60:
                        covered_concepts = covered_concepts[-60:]

                    for el in elements:
                        if el.get("type") == "callout" and "Practice" in el.get("callout_type", ""):
                            all_practice.append({"id": topic_id, "content": el.get("content", "")})

                    # Notify caller of partial progress (for background worker sync)
                    if partial_update_cb:
                        partial_update_cb(all_notes)

                ctx = {topic_id: item.get("title", "")}
                if parent_ctx:
                    ctx.update(parent_ctx)

                for key in ("subtopics", "subsubtopics", "subsubsubtopics"):
                    if key in item and item[key]:
                        _generate_for_list(item[key], ctx)

        try:
            _generate_for_list(lesson_plan.get("topics", []))
        except PauseRequested:
            logger.info("Detailed notes generation paused by user.")

        answer_key = None
        if all_practice:
            prompt = "You are an expert teaching assistant. Create a detailed, step-by-step answer key for the following practice problems. Clearly label the answer for each topic ID.\n\n"
            for i, prob in enumerate(all_practice):
                prompt += f"--- Problem {i + 1} (from Topic {prob['id']}) ---\n{prob['content']}\n\n"
            try:
                answer_key = self._generate_text(prompt, temperature, "answer_key")
            except Exception as e:
                logger.warning(f"Failed to generate answer key: {e}")

        # Image fetch phase — parallel pipeline, separate UI phase
        def _img_progress(done, img_total, msg):
            if progress_cb and img_total > 0:
                progress_cb(-1, img_total, f"🖼️ {msg}")

        from .image_downloader import populate_images_parallel
        populate_images_parallel(all_notes, progress_cb=_img_progress)

        return all_notes, answer_key

    # ── Global context & length budget helpers ───────────────────────────────
    def _build_global_context(self, roadmap_tree: Dict, current_topic_id: str,
                              completed_ids: List[str] = None) -> str:
        """Build a compressed course-map string injected into every notes prompt."""
        completed_ids = completed_ids or []
        all_topics: List[Dict] = []

        def _flatten(nodes, depth=0):
            for n in nodes:
                all_topics.append({"id": n["id"], "title": n["title"], "depth": depth,
                                   "has_children": bool(n.get("subtopics"))})
                for key in ("subtopics",):
                    if key in n and n[key]:
                        _flatten(n[key], depth + 1)

        _flatten(roadmap_tree.get("topics", []))

        lines = ["COURSE MAP (your position in the book):"]
        current_idx = -1
        for idx, t in enumerate(all_topics):
            indent = "  " * t["depth"]
            role = "INTRO" if t["has_children"] else "CORE"
            status = "✓" if t["id"] in completed_ids else ("→ CURRENT" if t["id"] == current_topic_id else "  ")
            lines.append(f"  {indent}{t['id']}: {t['title']} [{role}] {status}")
            if t["id"] == current_topic_id:
                current_idx = idx

        if current_idx >= 0:
            upcoming = [t for t in all_topics[current_idx + 1:]
                        if t["id"] not in completed_ids][:3]
            if upcoming:
                lines.append("COMING UP NEXT:")
                for t in upcoming:
                    lines.append(f"  {t['id']}: {t['title']}")

        return "\n".join(lines)

    @staticmethod
    def _estimate_target_length(depth: int, has_children: bool,
                                is_highlighted: bool = False) -> Dict[str, int]:
        """Return a {min_words, max_words} dict based on node role."""
        if has_children:
            return {"min_words": 200, "max_words": 400,
                    "label": "concise overview (200-400 words)"}
        if is_highlighted:
            return {"min_words": 1000, "max_words": 1500,
                    "label": "detailed deep-dive (1000-1500 words)"}
        if depth >= 3:
            return {"min_words": 400, "max_words": 700,
                    "label": "focused explanation (400-700 words)"}
        return {"min_words": 600, "max_words": 1000,
                "label": "thorough explanation (600-1000 words)"}

    def _build_transition_context(self, prev_title: str, prev_snippet: str,
                                  current_title: str) -> str:
        """Build a rich transition instruction for the next section prompt."""
        return (
            f"CONTINUITY CONTEXT (do not quote or reference explicitly):\n"
            f"Last generated section title: \"{prev_title}\"\n"
            f"Tail context from the book:\n"
            f"\"\"\"\n{prev_snippet}\n\"\"\"\n"
            f"Task for current section \"{current_title}\": Continue seamlessly from this state, "
            f"preserve narrative flow, and add only new learning value. "
            f"Do not include meta phrases such as 'in the previous section' or 'as discussed earlier'.\n"
        )
