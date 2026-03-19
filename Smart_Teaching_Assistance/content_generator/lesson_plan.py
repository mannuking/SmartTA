"""
Stage 2 — Lesson plan generation (recursive per-topic chunks).
"""

import logging
import json
import re
from typing import Dict, List, Optional, Callable

from .constants import LESSON_PLAN_STRUCTURE_TAGS
from Smart_Teaching_Assistance import PauseRequested

logger = logging.getLogger(__name__)


class LessonPlanMixin:
    """Lesson plan generation methods."""

    @staticmethod
    def _topic_ids_in_subtree(topic_data: Dict) -> List[str]:
        """Return all topic IDs in a roadmap subtree."""
        ids: List[str] = []

        def _walk(node: Dict):
            topic_id = node.get("id")
            if topic_id:
                ids.append(topic_id)
            for child in node.get("subtopics", []) or []:
                _walk(child)

        _walk(topic_data)
        return ids

    @staticmethod
    def _format_topic_scope(topic_data: Dict) -> str:
        """Render a chapter scope outline for the chapter-blueprint prompt."""
        lines: List[str] = []

        def _walk(node: Dict, depth: int = 0):
            indent = "  " * depth
            lines.append(f"{indent}- {node.get('id', '')}: {node.get('title', 'Untitled')}")
            for child in node.get("subtopics", []) or []:
                _walk(child, depth + 1)

        _walk(topic_data)
        return "\n".join(lines)

    @staticmethod
    def _coerce_bullets(value) -> str:
        """Convert list/str values into markdown bullet list text."""
        if isinstance(value, list):
            out = [str(v).strip() for v in value if str(v).strip()]
            return "\n".join(f"- {v}" for v in out)
        if isinstance(value, str):
            return value.strip()
        return ""

    @staticmethod
    def _empty_structured_content() -> Dict[str, str]:
        return {k: "" for k in LESSON_PLAN_STRUCTURE_TAGS.keys()}

    @staticmethod
    def _word_count(text: str) -> int:
        return len((text or "").split())

    def _render_coverage_contract(self, contract: Dict) -> str:
        """Build markdown for section-level non-redundancy and continuity rules."""
        if not isinstance(contract, dict):
            return ""

        prereq = self._coerce_bullets(contract.get("prerequisites", []))
        must_include = self._coerce_bullets(contract.get("must_include", []))
        must_exclude = self._coerce_bullets(contract.get("must_exclude", []))
        bridge_in = str(contract.get("bridge_from_previous", "")).strip()
        bridge_out = str(contract.get("handoff_to_next", "")).strip()

        parts: List[str] = []
        if prereq:
            parts.append(f"### Prerequisites\n{prereq}")
        if must_include:
            parts.append(f"### Must Include\n{must_include}")
        if must_exclude:
            parts.append(f"### Must Exclude (Avoid Redundancy)\n{must_exclude}")
        if bridge_in:
            parts.append(f"### Continuity Bridge From Previous\n{bridge_in}")
        if bridge_out:
            parts.append(f"### Continuity Handoff To Next\n{bridge_out}")

        if not parts:
            return ""
        return "\n\n".join(parts)

    def _compose_section_content(
        self,
        structured_content: Dict[str, str],
        teaching_prompt: str,
        coverage_contract: Dict,
    ) -> str:
        """Compose editable markdown content for each lesson-plan section."""
        sections: List[str] = []

        core = self._reconstruct_markdown(structured_content)
        if core:
            sections.append(core)

        teaching_prompt = (teaching_prompt or "").strip()
        if teaching_prompt:
            sections.append(f"### Section Mega Prompt\n{teaching_prompt}")

        contract_md = self._render_coverage_contract(coverage_contract)
        if contract_md:
            sections.append(contract_md)

        return "\n\n".join(s for s in sections if s).strip()

    @staticmethod
    def _extract_json_payload(raw: str):
        """Extract JSON object from a model response with fence/noise tolerance."""
        if not raw:
            return None
        cleaned = raw.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'```\s*$', '', cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except Exception:
            pass

        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first >= 0 and last > first:
            subset = cleaned[first:last + 1]
            try:
                return json.loads(subset)
            except Exception:
                return None
        return None

    def _parse_chapter_blueprint(self, raw: str) -> Dict[str, Dict]:
        """Parse chapter blueprint JSON into section data keyed by topic ID."""
        payload = self._extract_json_payload(raw)
        if not isinstance(payload, dict):
            return {}

        sections = payload.get("sections", [])
        if not isinstance(sections, list):
            return {}

        parsed: Dict[str, Dict] = {}
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sid = str(sec.get("id", "")).strip()
            if not sid:
                continue

            raw_struct = sec.get("structured_content", {})
            if not isinstance(raw_struct, dict):
                raw_struct = {}

            structured = self._empty_structured_content()
            for key in structured.keys():
                val = raw_struct.get(key, sec.get(key, ""))
                structured[key] = self._coerce_bullets(val)

            contract = sec.get("coverage_contract", {})
            if not isinstance(contract, dict):
                contract = {}

            teaching_prompt = str(sec.get("teaching_prompt", "")).strip()
            if not teaching_prompt:
                teaching_prompt = str(sec.get("mega_prompt", "")).strip()
            title = str(sec.get("title", "")).strip()

            parsed[sid] = {
                "id": sid,
                "title": title,
                "structured_content": structured,
                "teaching_prompt": teaching_prompt,
                "coverage_contract": contract,
            }

        return parsed

    def _build_tree_from_blueprint(
        self,
        roadmap_node: Dict,
        section_map: Dict[str, Dict],
        existing_chunks: Optional[Dict[str, Dict]] = None,
    ) -> Dict:
        """Map chapter blueprint output back onto the roadmap tree shape."""
        existing_chunks = existing_chunks or {}

        topic_id = roadmap_node.get("id", "")
        title = roadmap_node.get("title", "Untitled")

        section = section_map.get(topic_id, {})
        existing = existing_chunks.get(topic_id, {})

        structured_content = section.get("structured_content")
        if not isinstance(structured_content, dict) or not any(structured_content.values()):
            structured_content = existing.get("structured_content", self._empty_structured_content())

        teaching_prompt = section.get("teaching_prompt", "")
        coverage_contract = section.get("coverage_contract", {})

        content = section.get("content", "").strip()
        if not content:
            content = self._compose_section_content(structured_content, teaching_prompt, coverage_contract)
        if not content and teaching_prompt:
            content = f"### Section Mega Prompt\n{teaching_prompt}"
        if not content:
            content = existing.get("content", "").strip()

        node = {
            "id": topic_id,
            "title": title,
            "structured_content": structured_content,
            "content": content,
            "teaching_prompt": teaching_prompt,
            "coverage_contract": coverage_contract,
            "subtopics": [],
        }

        for child in roadmap_node.get("subtopics", []) or []:
            node["subtopics"].append(
                self._build_tree_from_blueprint(child, section_map, existing_chunks)
            )

        return node

    def _generate_chapter_blueprint(
        self,
        subject: str,
        difficulty_level: str,
        chapter_topic: Dict,
        temperature: float,
    ) -> Dict[str, Dict]:
        """Generate one detailed lesson-plan blueprint for an entire top-level chapter."""
        chapter_id = chapter_topic.get("id", "")
        chapter_title = chapter_topic.get("title", "Untitled")
        chapter_scope = self._format_topic_scope(chapter_topic)
        section_count = len(self._topic_ids_in_subtree(chapter_topic))
        # Scale output budget for larger chapters to reduce truncation risk.
        max_tokens = min(32768, max(8192, section_count * 1400))

        ordered_lines: List[str] = []
        id_title_pairs: List[tuple] = []

        def _flatten(node: Dict, depth: int = 0):
            tid = node.get("id", "")
            ttitle = node.get("title", "Untitled")
            if tid:
                ordered_lines.append(f"- {tid}: {ttitle}")
                id_title_pairs.append((tid, ttitle))
            for child in node.get("subtopics", []) or []:
                _flatten(child, depth + 1)

        _flatten(chapter_topic)

        sibling_map: Dict[str, List[str]] = {}
        parent_map: Dict[str, str] = {}
        children_map: Dict[str, List[str]] = {}

        def _walk(node: Dict, parent_id: str = ""):
            tid = node.get("id", "")
            kids = [c.get("id", "") for c in (node.get("subtopics", []) or []) if c.get("id")]
            if tid:
                parent_map[tid] = parent_id
                children_map[tid] = kids
                if parent_id:
                    siblings = children_map.get(parent_id, [])
                    sibling_map[tid] = [s for s in siblings if s != tid]
                else:
                    sibling_map[tid] = []
            for child in node.get("subtopics", []) or []:
                _walk(child, tid)

        _walk(chapter_topic)

        relationship_lines: List[str] = []
        for tid, ttitle in id_title_pairs:
            relationship_lines.append(
                f"- {tid} | parent: {parent_map.get(tid) or 'NONE'} | "
                f"siblings: {', '.join(sibling_map.get(tid, [])) or 'NONE'} | "
                f"children: {', '.join(children_map.get(tid, [])) or 'NONE'}"
            )

        ordered_text = "\n".join(ordered_lines)
        relationships_text = "\n".join(relationship_lines)

        prompt = f"""You are a world-class curriculum architect.
Create a complete, detailed chapter lesson-plan blueprint for {subject}.

Audience: {difficulty_level}
Chapter: {chapter_id} — {chapter_title}

CHAPTER TO COVER (include ALL nodes exactly once):
{chapter_scope}

ORDERED SECTION LIST:
{ordered_text}

SECTION RELATIONSHIPS:
{relationships_text}

Output MUST be valid JSON with this exact top-level schema:
{{
  "chapter_id": "{chapter_id}",
  "sections": [
    {{
      "id": "Tx(.x)*",
      "title": "Section title",
            "mega_prompt": "A very detailed prompt defining exactly what to teach in this section"
    }}
  ]
}}

Quality constraints:
1) Every node in the chapter scope must appear exactly once in sections.
2) No redundancy across sections; each mega_prompt must include strict scope boundaries.
3) Student journey must progress from foundational intuition to exam-ready and advanced depth.
4) Keep continuity strong: each section should include bridge-from-previous and handoff-to-next guidance.
5) Keep mega_prompt practical and actionable for notes generation.
6) Target length per mega_prompt: around 800-1200 words where possible.
7) Keep JSON simple: only id, title, mega_prompt for each section.

MANDATORY INTERNAL TEMPLATE INSIDE EACH mega_prompt:
- Scope (what this section must teach)
- Explicit Exclusions (what belongs to siblings/parent/children and must not be duplicated)
- Prerequisite Anchor (what from parent/previous section is assumed)
- Teaching Sequence (ordered flow)
- Depth Targets (foundation, exam, advanced)
- Worked Examples + Counterexamples
- Misconceptions and fixes
- Assessment and exam focus
- Transition In and Transition Out

Return only JSON. No markdown code fences.
"""

        raw = self._generate_text(
            prompt,
            temperature,
            f"lp_chapter_{chapter_id}",
            max_output_tokens=max_tokens,
        )
        return self._parse_chapter_blueprint(raw)

    def generate_lesson_plan_chunk(self, subject: str, difficulty_level: str,
                                   topic_data: Dict, temperature: float = 0.3,
                                   parent_context: Optional[Dict] = None,
                                   depth: int = 1,
                                   progress_cb: Optional[Callable] = None,
                                   counter: Optional[List[int]] = None,
                                   total: int = 0,
                                   existing_chunks: Optional[Dict[str, Dict]] = None) -> Dict:
        """
        Generate a structured lesson plan chunk for a single roadmap node.
        Recurses into subtopics. Progress is reported per AI call.
        """
        topic_id = topic_data["id"]
        title = topic_data["title"]

        # ── RESUME CHECK ─────────────────────────────────────────────────────
        if existing_chunks and topic_id in existing_chunks:
            logger.info(f"Resuming: Skipping generation for {topic_id} (already exists)")
            chunk = existing_chunks[topic_id]
            if counter is not None:
                counter[0] += 1
                if progress_cb:
                    progress_cb(counter[0], total, f"Loaded: {topic_id} — {title}")
            return chunk

        logger.info(f"Generating lesson plan chunk for topic {topic_id}: {title} (Depth: {depth})...")

        if counter is not None and progress_cb:
            counter[0] += 1
            progress_cb(counter[0], total, f"Generating: {topic_id} — {title}")

        prompt = f"""You are an expert educator creating a CONCISE, HIGH-IMPACT LESSON PLAN CHUNK for: "{topic_id}: {title}".
Target audience: {difficulty_level}

Format and Content Requirements:
1. Focus ONLY on the most critical concepts.
2. Be concise and direct. logic over fluff.
3. Output usage strictly these structural tags:

[LEARNING_OBJECTIVES_START] — Clear, measurable objectives [LEARNING_OBJECTIVES_END]
[KEY_CONCEPTS_DEFINITIONS_START] — Core importance terms only [KEY_CONCEPTS_DEFINITIONS_END]
[INSTRUCTIONAL_CONTENT_OUTLINE_START] — Strategic teaching outline [INSTRUCTIONAL_CONTENT_OUTLINE_END]
[ENGAGEMENT_STRATEGIES_ACTIVITIES_START] — High-value active learning [ENGAGEMENT_STRATEGIES_ACTIVITIES_END]
[ASSESSMENT_METHODS_CHECKPOINTS_START] — Evaluation methods [ASSESSMENT_METHODS_CHECKPOINTS_END]
[POTENTIAL_CHALLENGES_MISCONCEPTIONS_START] — Common pitfalls [POTENTIAL_CHALLENGES_MISCONCEPTIONS_END]
[ELI5_COMPLEX_CONCEPTS_START] — Simple explanations [ELI5_COMPLEX_CONCEPTS_END]
[EDUCATOR_NOTES_START] — Teaching tips and notes [EDUCATOR_NOTES_END]

Be comprehensive and scholarly. Content within each tag should use Markdown formatting.
"""
        if parent_context:
            prompt += "\nContext from parent topics (do not repeat):\n"
            for pid, pdesc in parent_context.items():
                prompt += f"  - {pid}: {pdesc}\n"

        raw = self._generate_text(prompt, temperature, f"lp_chunk_{topic_id}")
        structured = self._parse_structured_content(raw, LESSON_PLAN_STRUCTURE_TAGS)
        markdown = self._reconstruct_markdown(structured)

        chunk: Dict = {
            "id": topic_id,
            "title": title,
            "structured_content": structured,
            "content": markdown,
            "teaching_prompt": "",
            "coverage_contract": {},
        }

        current_ctx = {topic_id: title}
        if parent_context:
            current_ctx.update(parent_context)

        if topic_data.get("subtopics"):
            chunk["subtopics"] = [
                self.generate_lesson_plan_chunk(
                    subject, difficulty_level, sub, temperature,
                    current_ctx, depth + 1,
                    progress_cb=progress_cb, counter=counter, total=total,
                    existing_chunks=existing_chunks
                )
                for sub in topic_data["subtopics"]
            ]

        return chunk

    def generate_lesson_plan(self, subject: str, roadmap_tree: Dict,
                             difficulty_level: str, temperature: float = 0.3,
                             progress_cb: Optional[Callable] = None,
                             existing_plan: Optional[Dict] = None,
                             partial_update_cb: Optional[Callable] = None) -> Dict:
        """Generate full lesson plan using one chapter-level call per top topic."""
        lesson_plan = {"subject": subject, "difficulty": difficulty_level, "topics": []}
        topics = roadmap_tree.get("topics", [])

        existing_chunks = {}
        if existing_plan:
            def _collect_existing(nodes):
                for n in nodes:
                    existing_chunks[n["id"]] = n
                    for key in ("subtopics", "subsubtopics", "subsubsubtopics"):
                        if key in n and n[key]:
                            _collect_existing(n[key])
            _collect_existing(existing_plan.get("topics", []))

        total = self._count_tree_items(topics)
        counter = [0]

        try:
            for topic in topics:
                chapter_id = topic.get("id", "")
                chapter_title = topic.get("title", "Untitled")
                chapter_topic_ids = self._topic_ids_in_subtree(topic)
                chapter_size = len(chapter_topic_ids)

                # Resume optimization: if entire chapter already exists, skip AI call.
                if chapter_topic_ids and all(tid in existing_chunks for tid in chapter_topic_ids):
                    chunk = self._build_tree_from_blueprint(topic, {}, existing_chunks=existing_chunks)
                    counter[0] += chapter_size
                    if progress_cb:
                        progress_cb(counter[0], total, f"Loaded chapter: {chapter_id} — {chapter_title}")
                    lesson_plan["topics"].append(chunk)
                    if partial_update_cb:
                        partial_update_cb(lesson_plan)
                    continue

                if progress_cb:
                    progress_cb(counter[0], total, f"Generating chapter blueprint: {chapter_id} — {chapter_title}")

                try:
                    section_map = self._generate_chapter_blueprint(
                        subject=subject,
                        difficulty_level=difficulty_level,
                        chapter_topic=topic,
                        temperature=temperature,
                    )
                    if not section_map:
                        raise ValueError(f"Empty chapter blueprint for {chapter_id}")

                    chunk = self._build_tree_from_blueprint(
                        topic,
                        section_map,
                        existing_chunks=existing_chunks,
                    )

                    counter[0] = min(total, counter[0] + chapter_size)
                    if progress_cb:
                        progress_cb(counter[0], total, f"Completed chapter: {chapter_id} — {chapter_title}")

                except Exception as chapter_err:
                    logger.warning(
                        f"Chapter blueprint failed for {chapter_id}; falling back to per-topic generation. Error: {chapter_err}"
                    )
                    chunk = self.generate_lesson_plan_chunk(
                        subject, difficulty_level, topic, temperature,
                        parent_context=None, depth=1,
                        progress_cb=progress_cb, counter=counter, total=total,
                        existing_chunks=existing_chunks
                    )

                lesson_plan["topics"].append(chunk)
                # Sync partial plan to caller (e.g. background worker)
                if partial_update_cb:
                    partial_update_cb(lesson_plan)
        except PauseRequested:
            logger.info("Lesson plan generation paused by user.")

        return lesson_plan
