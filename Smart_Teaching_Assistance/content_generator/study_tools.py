"""
Study tools — chapter summaries, flashcards, question bank, practice exams, glossary.
"""

import re
import json
import logging
from typing import Dict, List, Optional, Callable

logger = logging.getLogger(__name__)


class StudyToolsMixin:
    """Post-generation study material generators."""

    # ── Chapter Summaries ────────────────────────────────────────────────────
    def generate_chapter_summaries(self, all_notes: List[Dict], subject: str,
                                   roadmap_tree: Dict,
                                   temperature: float = 0.3,
                                   progress_cb: Optional[Callable] = None) -> Dict[str, Dict]:
        """Generate concise chapter summaries for each top-level topic."""
        top_topics = roadmap_tree.get("topics", [])
        summaries: Dict[str, Dict] = {}

        for idx, chapter in enumerate(top_topics):
            chapter_id = chapter["id"]
            chapter_title = chapter["title"]

            if progress_cb:
                progress_cb(idx + 1, len(top_topics), f"Summary: {chapter_id} — {chapter_title}")

            chapter_content_parts = []
            for note in all_notes:
                if note["id"] == chapter_id or note["id"].startswith(chapter_id + "."):
                    for el in note.get("elements", []):
                        if el.get("type") in ("text", "heading", "callout"):
                            chapter_content_parts.append(el.get("content", ""))
            chapter_text = "\n".join(chapter_content_parts)[:6000]

            if not chapter_text.strip():
                continue

            prompt = f"""You are a textbook editor writing a chapter summary for "{chapter_title}" in {subject}.

Chapter content to summarize:
---
{chapter_text}
---

Create a JSON response with:
{{
  "summary": "A 150-200 word executive summary covering the main ideas, their relationships, and practical significance.",
  "key_concepts": ["concept1", "concept2", "concept3", "concept4", "concept5"],
  "learning_outcomes": ["By the end of this chapter, students can...", "..."],
  "connections_to_next": "One sentence connecting this chapter to what comes next."
}}

Output ONLY valid JSON. No markdown code blocks."""

            try:
                raw = self._generate_text(prompt, temperature, f"chapter_summary_{chapter_id}")
                cleaned = raw.strip()
                if cleaned.startswith("```json"): cleaned = cleaned[7:]
                elif cleaned.startswith("```"): cleaned = cleaned[3:]
                if cleaned.endswith("```"): cleaned = cleaned[:-3]
                data = json.loads(cleaned.strip())
                summaries[chapter_id] = {
                    "title": chapter_title,
                    "summary": data.get("summary", ""),
                    "key_concepts": data.get("key_concepts", []),
                    "learning_outcomes": data.get("learning_outcomes", []),
                    "connections_to_next": data.get("connections_to_next", ""),
                }
            except Exception as e:
                logger.warning(f"Failed to generate summary for {chapter_id}: {e}")
                summaries[chapter_id] = {
                    "title": chapter_title,
                    "summary": f"Summary for {chapter_title} could not be generated.",
                    "key_concepts": [], "learning_outcomes": [],
                    "connections_to_next": "",
                }

        return summaries

    # ── Flashcards ───────────────────────────────────────────────────────────
    def generate_flashcards(self, all_notes: List[Dict], subject: str,
                            temperature: float = 0.3,
                            progress_cb: Optional[Callable] = None) -> List[Dict]:
        """Extract and generate flashcards from all notes."""
        auto_cards: List[Dict] = []
        for note in all_notes:
            for el in note.get("elements", []):
                if el.get("type") == "equation" and el.get("content"):
                    auto_cards.append({
                        "topic_id": note["id"], "topic_title": note["title"],
                        "content": el["content"], "card_type": "equation",
                    })
                if el.get("type") == "callout" and "Takeaway" in el.get("callout_type", ""):
                    auto_cards.append({
                        "topic_id": note["id"], "topic_title": note["title"],
                        "content": el["content"], "card_type": "concept",
                    })

        all_flashcards: List[Dict] = []
        batch_size = 5
        topic_batches = [all_notes[i:i + batch_size] for i in range(0, len(all_notes), batch_size)]

        for batch_idx, batch in enumerate(topic_batches):
            if progress_cb:
                progress_cb(batch_idx + 1, len(topic_batches),
                            f"Flashcards: batch {batch_idx + 1}/{len(topic_batches)}")

            content_block = ""
            for note in batch:
                content_block += f"\n--- {note['id']}: {note['title']} ---\n"
                for el in note.get("elements", []):
                    if el.get("type") in ("text", "callout", "heading"):
                        content_block += el.get("content", "") + "\n"
            content_block = content_block[:8000]

            prompt = f"""You are creating study flashcards for {subject}.

From the content below, generate 10-15 high-quality flashcards that would help students prepare for university exams.

Content:
---
{content_block}
---

Rules:
- Mix card types: definition, concept, application, comparison, formula
- Include difficulty levels: easy, medium, hard
- "Front" = question or prompt. "Back" = concise answer.
- For formulas, put the name on front, the formula + when to use it on back.
- For definitions, put the term on front, the full definition on back.
- For concepts, test understanding, not mere recall.

Output ONLY valid JSON array:
[
  {{"front": "...", "back": "...", "topic_id": "T1.2", "card_type": "definition", "difficulty": "easy"}},
  ...
]
No markdown code blocks."""

            try:
                raw = self._generate_text(prompt, temperature, f"flashcards_batch_{batch_idx}")
                cleaned = raw.strip()
                if cleaned.startswith("```json"): cleaned = cleaned[7:]
                elif cleaned.startswith("```"): cleaned = cleaned[3:]
                if cleaned.endswith("```"): cleaned = cleaned[:-3]
                cards = json.loads(cleaned.strip())
                if isinstance(cards, dict):
                    # LLM sometimes wraps in {"cards": [...]} or {"flashcards": [...]}
                    for key in ("cards", "flashcards", "data"):
                        if key in cards and isinstance(cards[key], list):
                            cards = cards[key]
                            break
                if isinstance(cards, list):
                    for card in cards:
                        card.setdefault("topic_title", "")
                        tid = card.get("topic_id", "")
                        for note in batch:
                            if note["id"] == tid:
                                card["topic_title"] = note["title"]
                                break
                    all_flashcards.extend(cards)
            except Exception as e:
                logger.warning(f"Failed to generate flashcards for batch {batch_idx}: {e}")

        logger.info(f"Generated {len(all_flashcards)} flashcards total.")
        return all_flashcards

    @staticmethod
    def flashcards_to_anki_csv(flashcards: List[Dict]) -> str:
        """Convert flashcards to Anki-importable CSV (tab-separated)."""
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output, delimiter='\t')
        for card in flashcards:
            tags = f"{card.get('topic_id', '')} {card.get('card_type', '')} {card.get('difficulty', '')}"
            writer.writerow([card.get("front", ""), card.get("back", ""), tags.strip()])
        return output.getvalue()

    # ── Question Bank ────────────────────────────────────────────────────────
    def generate_question_bank(self, all_notes: List[Dict], subject: str,
                               difficulty_level: str,
                               temperature: float = 0.4,
                               progress_cb: Optional[Callable] = None) -> List[Dict]:
        """Generate a comprehensive question bank from all notes."""
        all_questions: List[Dict] = []

        leaf_notes = [n for n in all_notes
                      if not any(other["id"].startswith(n["id"] + ".")
                                 for other in all_notes if other["id"] != n["id"])]

        for idx, note in enumerate(leaf_notes):
            if progress_cb:
                progress_cb(idx + 1, len(leaf_notes),
                            f"Questions: {note['id']} — {note['title']}")

            content = "\n".join(
                el.get("content", "") for el in note.get("elements", [])
                if el.get("type") in ("text", "callout", "equation", "heading")
            )[:4000]

            if not content.strip():
                continue

            prompt = f"""You are creating exam questions for {subject} at {difficulty_level} level.

Topic: {note['id']}: {note['title']}
Content:
---
{content}
---

Generate 5 questions spanning Bloom's taxonomy:
1. REMEMBER: One factual recall question (MCQ or fill-in-blank)
2. UNDERSTAND: One conceptual understanding question (short answer)
3. APPLY: One application/problem-solving question (numerical or scenario)
4. ANALYZE: One analysis question requiring comparison or breakdown
5. EVALUATE/CREATE: One higher-order question (essay or design problem)

Output ONLY valid JSON array:
[
  {{
    "question": "Full question text",
    "answer": "Model answer (detailed enough for a marking scheme)",
    "topic_id": "{note['id']}",
    "bloom_level": "remember|understand|apply|analyze|evaluate",
    "difficulty": "easy|medium|hard",
    "question_type": "mcq|short_answer|numerical|essay|scenario",
    "marks": 2,
    "estimated_time_min": 3
  }},
  ...
]
No markdown code blocks."""

            try:
                raw = self._generate_text(prompt, temperature, f"qbank_{note['id']}")
                cleaned = raw.strip()
                if cleaned.startswith("```json"): cleaned = cleaned[7:]
                elif cleaned.startswith("```"): cleaned = cleaned[3:]
                if cleaned.endswith("```"): cleaned = cleaned[:-3]
                questions = json.loads(cleaned.strip())
                if isinstance(questions, list):
                    for q in questions:
                        q["topic_title"] = note["title"]
                    all_questions.extend(questions)
            except Exception as e:
                logger.warning(f"Failed to generate questions for {note['id']}: {e}")

        logger.info(f"Generated {len(all_questions)} questions total.")
        return all_questions

    # ── Practice Exam ────────────────────────────────────────────────────────
    def generate_practice_exam(self, question_bank: List[Dict], subject: str,
                               num_questions: int = 20,
                               time_limit_min: int = 120,
                               difficulty_distribution: Optional[Dict] = None) -> Dict:
        """Assemble a practice exam from the question bank."""
        if not difficulty_distribution:
            difficulty_distribution = {"easy": 0.3, "medium": 0.4, "hard": 0.3}

        selected: List[Dict] = []
        by_difficulty = {"easy": [], "medium": [], "hard": []}
        for q in question_bank:
            d = q.get("difficulty", "medium").lower()
            by_difficulty.setdefault(d, []).append(q)

        import random as _rand
        for diff, fraction in difficulty_distribution.items():
            target = max(1, int(num_questions * fraction))
            pool = by_difficulty.get(diff, [])
            _rand.shuffle(pool)
            selected.extend(pool[:target])

        selected = selected[:num_questions]
        total_marks = sum(q.get("marks", 2) for q in selected)

        exam = {
            "title": f"{subject} — Practice Examination",
            "instructions": (
                f"Time Allowed: {time_limit_min} minutes\n"
                f"Total Marks: {total_marks}\n"
                f"Total Questions: {len(selected)}\n\n"
                f"Answer ALL questions. Show your working where applicable.\n"
                f"Marks for each question are indicated in parentheses."
            ),
            "questions": [
                {
                    "number": i + 1,
                    "question": q.get("question", ""),
                    "marks": q.get("marks", 2),
                    "bloom_level": q.get("bloom_level", ""),
                    "topic_id": q.get("topic_id", ""),
                    "estimated_time_min": q.get("estimated_time_min", 3),
                }
                for i, q in enumerate(selected)
            ],
            "total_marks": total_marks,
            "time_limit_min": time_limit_min,
            "answer_key": [
                {
                    "number": i + 1,
                    "question": q.get("question", ""),
                    "answer": q.get("answer", ""),
                    "marks": q.get("marks", 2),
                }
                for i, q in enumerate(selected)
            ],
        }
        return exam

    # ── Glossary ─────────────────────────────────────────────────────────────
    @staticmethod
    def extract_glossary(all_notes: List[Dict]) -> List[Dict]:
        """Auto-extract key terms and definitions from all notes."""
        glossary: Dict[str, Dict] = {}

        for note in all_notes:
            for el in note.get("elements", []):
                content = el.get("content", "")
                if not content:
                    continue

                for match in re.finditer(
                    r'\*\*([^*]{2,60})\*\*\s*[:—–-]\s*(.{20,300}?)(?:\.|$)',
                    content
                ):
                    term = match.group(1).strip()
                    defn = match.group(2).strip()
                    key = term.lower()
                    if key not in glossary:
                        glossary[key] = {
                            "term": term, "definition": defn,
                            "source_topic_id": note["id"],
                            "source_topic_title": note["title"],
                        }

                for match in re.finditer(
                    r'(?:^|\. )([A-Z][^.]{2,50}?)\s+(?:is defined as|refers to|is)\s+(.{20,200}?)(?:\.|$)',
                    content
                ):
                    term = match.group(1).strip()
                    defn = match.group(2).strip()
                    key = term.lower()
                    if key not in glossary and len(term.split()) <= 6:
                        glossary[key] = {
                            "term": term, "definition": defn,
                            "source_topic_id": note["id"],
                            "source_topic_title": note["title"],
                        }

        sorted_glossary = sorted(glossary.values(), key=lambda x: x["term"].lower())
        logger.info(f"Extracted {len(sorted_glossary)} glossary terms.")
        return sorted_glossary
