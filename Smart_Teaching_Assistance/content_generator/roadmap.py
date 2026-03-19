"""
Stage 1 — Roadmap generation, parsing, and storage.
"""

import re
import json
import hashlib
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class RoadmapMixin:
    """Roadmap generation and parsing methods."""

    def generate_roadmap(self, source_text: str, difficulty_level: str,
                         subject: str = "", temperature: float = 0.3) -> str:
        """
        Generate hierarchical roadmap with T-numbering from syllabus/PDF text.

        Returns:
            Formatted roadmap string with T-numbering
        """
        prompt = f"""You are an expert curriculum designer. Based on the subject '{subject}', the syllabus below, and a target audience of '{difficulty_level}' students, create a hierarchical roadmap.
Syllabus:
---
{source_text[:30000]}
---
Output MUST be in the format:
Sequence: <Linear, Spiral, or Modular>
T<number>: Main Topic
    T<number>.<number>: Subtopic
        T<number>.<number>.<number>: Detail
"""
        logger.info(f"Generating roadmap for {subject} (Diff: {difficulty_level})...")
        cache_key = f"roadmap_{subject}_{difficulty_level}_{source_text[:500]}"
        if cache_key in self._cache:
            logger.info("Returning cached roadmap.")
            return self._cache[cache_key]

        try:
            roadmap_text = self._generate_text(prompt, temperature, purpose="roadmap")
            logger.info(f"Roadmap generated ({len(roadmap_text)} chars).")
            if "T1:" in roadmap_text:
                self._cache[cache_key] = roadmap_text
                return roadmap_text
            else:
                logger.error(f"Roadmap validation failed — T1: not found. Output start: {roadmap_text[:300]}")
                return roadmap_text  # return anyway, let UI handle
        except Exception as e:
            logger.error(f"Error generating roadmap: {e}")
            raise

    def parse_roadmap(self, roadmap_text: str) -> Dict:
        """
        Parse T-numbered roadmap text into structured dictionary.

        Returns:
            Dictionary with keys: sequence, topics (list of tree nodes)
        """
        roadmap = {"sequence": "Linear", "topics": []}
        lines = roadmap_text.split("\n")
        if lines and lines[0].startswith("Sequence:"):
            roadmap["sequence"] = lines[0].replace("Sequence:", "").strip()
            lines = lines[1:]

        topic_hierarchy: Dict[str, Dict] = {}
        logger.info("Parsing roadmap text...")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = re.match(r"^\s*(T(\d+(\.\d+)*)):\s*(.+)$", line)
            if match:
                topic_id = match.group(1)
                parts = topic_id[1:].split(".")
                title = match.group(4).strip()
                node = {"id": topic_id, "title": title, "subtopics": []}

                if len(parts) == 1:
                    roadmap["topics"].append(node)
                    topic_hierarchy[topic_id] = node
                else:
                    parent_id_parts = parts[:-1]
                    parent_id = "T" + ".".join(parent_id_parts)

                    parent_node = topic_hierarchy.get(parent_id)
                    if parent_node:
                        parent_node["subtopics"].append(node)
                        topic_hierarchy[topic_id] = node
                    else:
                        logger.warning(f"Parent node {parent_id} not found for {topic_id}. Adding as top-level.")
                        roadmap["topics"].append(node)
                        topic_hierarchy[topic_id] = node
        logger.info(f"Roadmap parsing complete. Found {len(roadmap['topics'])} top-level topics.")
        return roadmap

    def parse_roadmap_flat(self, roadmap_text: str) -> Dict:
        """
        Parse roadmap text into flat {topic: {subtopic: [sub-subtopics]}} dict
        (backward-compatible with old arrow format).
        """
        structure = {}
        current_topic = None
        current_subtopic = None

        logger.info("Parsing flat roadmap text...")
        for line in roadmap_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if '->' in line:
                parts = [p.strip() for p in line.split('->')]
                if len(parts) == 1:
                    current_topic = parts[0]
                    structure.setdefault(current_topic, {})
                elif len(parts) == 2:
                    current_topic, current_subtopic = parts[0], parts[1]
                    structure.setdefault(current_topic, {})
                    structure[current_topic].setdefault(current_subtopic, [])
                elif len(parts) >= 3:
                    current_topic, current_subtopic = parts[0], parts[1]
                    structure.setdefault(current_topic, {})
                    structure[current_topic].setdefault(current_subtopic, [])
                    structure[current_topic][current_subtopic].append(parts[2])
            else:
                match = re.match(r"^\s*T(\d+(\.\d+)*):\s*(.+)$", line)
                if match:
                    nums = match.group(1).split(".")
                    title = match.group(3).strip()
                    if len(nums) == 1:
                        current_topic = title
                        structure.setdefault(current_topic, {})
                    elif len(nums) == 2:
                        current_subtopic = title
                        if current_topic:
                            structure.setdefault(current_topic, {})
                            structure[current_topic].setdefault(current_subtopic, [])
                    elif len(nums) >= 3:
                        if current_topic and current_subtopic:
                            structure.setdefault(current_topic, {})
                            structure[current_topic].setdefault(current_subtopic, [])
                            structure[current_topic][current_subtopic].append(title)
        logger.info(f"Flat roadmap parsing complete. Found {len(structure)} top-level items.")
        return structure

    def store_roadmap(self, roadmap_text: str, roadmap_structure: Dict,
                      difficulty_level: str, source_type: str = "syllabus",
                      source_hash: Optional[str] = None) -> int:
        """Store roadmap in database. Returns ID."""
        if self.db is None:
            raise ValueError("Database connection required to store roadmap")
        structure_json = json.dumps(roadmap_structure)
        cursor = self.db.execute(
            """INSERT INTO roadmaps (user_id, source_type, source_hash, difficulty_level,
               roadmap_text, roadmap_structure) VALUES (?, ?, ?, ?, ?, ?)""",
            (self.user_id, source_type, source_hash, difficulty_level, roadmap_text, structure_json),
        )
        roadmap_id = cursor.lastrowid
        logger.info(f"Roadmap stored with ID: {roadmap_id}")
        return roadmap_id

    def get_roadmap(self, roadmap_id: int) -> Optional[Dict]:
        """Retrieve roadmap from database by ID."""
        if self.db is None:
            raise ValueError("Database connection required to retrieve roadmap")
        row = self.db.fetchone("SELECT * FROM roadmaps WHERE id = ? AND user_id = ?", (roadmap_id, self.user_id))
        if row is None:
            return None
        return {
            "id": row["id"],
            "source_type": row["source_type"],
            "source_hash": row["source_hash"],
            "difficulty_level": row["difficulty_level"],
            "roadmap_text": row["roadmap_text"],
            "roadmap_structure": json.loads(row["roadmap_structure"]),
            "created_at": row["created_at"],
        }

    def generate_and_store_roadmap(self, source_text: str, difficulty_level: str,
                                   source_type: str = "syllabus",
                                   subject: str = "") -> Dict:
        """Generate roadmap and store it in database."""
        roadmap_text = self.generate_roadmap(source_text, difficulty_level, subject=subject)
        roadmap_tree = self.parse_roadmap(roadmap_text)
        roadmap_flat = self.parse_roadmap_flat(roadmap_text)
        source_hash = hashlib.md5(source_text.encode()).hexdigest()

        roadmap_id = None
        if self.db is not None:
            roadmap_id = self.store_roadmap(
                roadmap_text, roadmap_flat, difficulty_level, source_type, source_hash
            )

        return {
            "roadmap_id": roadmap_id,
            "roadmap_text": roadmap_text,
            "roadmap_tree": roadmap_tree,
            "roadmap_structure": roadmap_flat,
            "source_hash": source_hash,
        }
