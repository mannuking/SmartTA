"""
Legacy / backward-compatible methods used by personalization and other consumers.
"""

import re
import json
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LegacyMixin:
    """Backward-compatible API surface kept for personalization, etc."""

    def generate_base_notes(self, topic: str, subtopic: str,
                            difficulty: str, context: str = "") -> Dict:
        """Generate comprehensive base notes with auto-tagging (simple API)."""
        prompt = f"""Generate comprehensive educational notes for:
Topic: {topic}
Subtopic: {subtopic}
Difficulty: {difficulty}
{f'Context: {context}' if context else ''}

Include:
1. Learning Objectives (use measurable action verbs)
2. Key Concepts and Definitions
3. Detailed Explanations
4. Examples (mark with [EXAMPLE:topic])
5. Analogies (mark with [ANALOGY:concept])
6. Applications (mark with [APPLICATION:field])
7. Case Studies (mark with [CASE_STUDY:domain])
8. Assessment Questions (varied types)

Use tags like [EXAMPLE:derivatives] to mark sections that can be personalized for different audiences.
Keep core definitions and theorems untagged."""

        try:
            response = self.model.generate_content(prompt)
            content = response.text
            tagged_content, tags = self.auto_tag_content(content)
            return {
                "content": tagged_content, "tags": tags,
                "topic": topic, "subtopic": subtopic, "difficulty": difficulty,
            }
        except Exception as e:
            logger.error(f"Error generating base notes: {e}")
            return {
                "content": "Error generating content", "tags": [],
                "topic": topic, "subtopic": subtopic, "difficulty": difficulty,
            }

    def auto_tag_content(self, content: str) -> Tuple[str, List[Dict]]:
        """Identify and tag personalizable sections."""
        tags: List[Dict] = []
        tag_pattern = r'\[(EXAMPLE|ANALOGY|APPLICATION|CASE_STUDY):([^\]]+)\]'
        for match in re.finditer(tag_pattern, content):
            tags.append({
                "tag_type": match.group(1),
                "context": match.group(2),
                "start_position": match.start(),
                "end_position": match.end(),
            })
        return content, tags

    def generate_multimodal_lesson(self, topic: str, subtopic: str,
                                   difficulty: str, pdf_hash: str,
                                   context: str = "", pdf_extractor=None,
                                   top_k: int = 5) -> Dict:
        """Generate lesson with matched images."""
        base_notes = self.generate_base_notes(topic, subtopic, difficulty, context)
        images: list = []
        if pdf_extractor and pdf_hash:
            try:
                relevant = pdf_extractor.find_relevant_images(
                    content_text=base_notes.get("content", ""),
                    pdf_hash=pdf_hash, top_k=top_k,
                )
                images = relevant
            except Exception as e:
                logger.error(f"Error finding relevant images: {e}")
        return {**base_notes, "images": images, "pdf_hash": pdf_hash}

    def store_base_notes(self, topic: str, subtopic: str,
                         difficulty_level: str, content: str,
                         tags: List[Dict], pdf_hash: Optional[str] = None,
                         associated_images: Optional[List[int]] = None) -> int:
        """Store base notes in database."""
        if self.db is None:
            raise ValueError("Database connection not initialized")
        tags_json = json.dumps(tags)
        images_json = json.dumps(associated_images if associated_images else [])
        cursor = self.db.execute(
            """INSERT INTO base_notes
               (topic, subtopic, difficulty_level, content, tags, pdf_hash, associated_images)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (topic, subtopic, difficulty_level, content, tags_json, pdf_hash, images_json),
        )
        logger.info(f"Stored base notes with ID: {cursor.lastrowid}")
        return cursor.lastrowid

    def get_base_notes(self, base_notes_id: int) -> Optional[Dict]:
        """Retrieve base notes from database."""
        if self.db is None:
            raise ValueError("Database connection not initialized")
        row = self.db.fetchone(
            """SELECT id, topic, subtopic, difficulty_level, content, tags,
                      pdf_hash, associated_images, created_at, updated_at
               FROM base_notes WHERE id = ?""",
            (base_notes_id,),
        )
        if row is None:
            return None
        return {
            "id": row["id"], "topic": row["topic"], "subtopic": row["subtopic"],
            "difficulty_level": row["difficulty_level"], "content": row["content"],
            "tags": json.loads(row["tags"]), "pdf_hash": row["pdf_hash"],
            "associated_images": json.loads(row["associated_images"]) if row["associated_images"] else [],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def generate_and_store_base_notes(self, topic: str, subtopic: str,
                                      difficulty: str, context: str = "",
                                      pdf_hash: Optional[str] = None,
                                      associated_images: Optional[List[int]] = None) -> Dict:
        """Generate base notes and store them in database."""
        base_notes = self.generate_base_notes(topic, subtopic, difficulty, context)
        if self.db:
            base_notes_id = self.store_base_notes(
                topic=topic, subtopic=subtopic, difficulty_level=difficulty,
                content=base_notes["content"], tags=base_notes["tags"],
                pdf_hash=pdf_hash, associated_images=associated_images,
            )
            base_notes["id"] = base_notes_id
        return base_notes
