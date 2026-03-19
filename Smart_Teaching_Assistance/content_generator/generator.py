"""
Content Generator -- thin facade that composes all mixin modules.

Pipeline stages:
  1. Roadmap  -- hierarchical T-numbered topic tree
  2. Lesson Plan -- structured pedagogical plan per node
  3. Detailed Lecture Notes -- rich tagged notes with anti-redundancy
  4. Review & Study Tools -- summaries, flashcards, question bank, etc.
"""

from ._base import BaseMixin
from .roadmap import RoadmapMixin
from .lesson_plan import LessonPlanMixin
from .lecture_notes import LectureNotesMixin
from .review import ReviewMixin
from .study_tools import StudyToolsMixin
from .rendering import RenderingMixin
from .legacy import LegacyMixin
from .constants import LESSON_PLAN_STRUCTURE_TAGS, LECTURE_NOTE_ELEMENT_TAGS, DOWNLOAD_DIR


class ContentGenerator(
    BaseMixin,
    RoadmapMixin,
    LessonPlanMixin,
    LectureNotesMixin,
    ReviewMixin,
    StudyToolsMixin,
    RenderingMixin,
    LegacyMixin,
):
    """
    Main entry-point class.

    All functionality lives in mixin modules -- this class simply inherits
    them so existing consumers continue to ``from content_generator import ContentGenerator``.
    """
    pass


# Re-export tag dicts at module level for any code that does
# ``from content_generator.generator import LESSON_PLAN_STRUCTURE_TAGS``
__all__ = [
    "ContentGenerator",
    "LESSON_PLAN_STRUCTURE_TAGS",
    "LECTURE_NOTE_ELEMENT_TAGS",
    "DOWNLOAD_DIR",
]