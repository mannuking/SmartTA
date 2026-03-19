"""
Shared constants, tag definitions, and configuration for content generation.
"""

from pathlib import Path

# Directory for downloaded images (project root / downloaded_images)
DOWNLOAD_DIR = Path(__file__).resolve().parents[2] / "downloaded_images"

# ──────────────────────────────────────────────────────────────────────────────
# TAG DEFINITIONS (from app2.py)
# ──────────────────────────────────────────────────────────────────────────────

LESSON_PLAN_STRUCTURE_TAGS = {
    "learning_objectives": "LEARNING_OBJECTIVES",
    "key_concepts": "KEY_CONCEPTS_DEFINITIONS",
    "instructional_content": "INSTRUCTIONAL_CONTENT_OUTLINE",
    "engagement_activities": "ENGAGEMENT_STRATEGIES_ACTIVITIES",
    "assessment_methods": "ASSESSMENT_METHODS_CHECKPOINTS",
    "potential_challenges": "POTENTIAL_CHALLENGES_MISCONCEPTIONS",
    "eli5_suggestion": "ELI5_COMPLEX_CONCEPTS",
    "additional_notes": "EDUCATOR_NOTES",
}

LECTURE_NOTE_ELEMENT_TAGS = {
    "heading_1": "H1",
    "heading_2": "H2",
    "heading_3": "H3",
    "paragraph": "P",
    "list_item": "LI",
    "code_block": "CODE",
    "quote_block": "QUOTE",
    "example_block": "EXAMPLE",
    "definition_block": "DEF",
    "equation_block": "EQ",
    "image_placeholder": "IMG",
    "table_placeholder": "TABLE",
    "summary_block": "SUMMARY",
    "analogy_block": "ANALOGY",
    "question_block": "QUESTION",
    "practice_problem": "PRACTICE",
}
