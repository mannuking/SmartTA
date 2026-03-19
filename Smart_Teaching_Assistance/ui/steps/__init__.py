"""Steps package — each step lives in its own module for maintainability.

Exports the same six functions that app.py expects:
    step_input, step_roadmap, step_lesson_plan,
    step_detailed_notes, step_personalize, step_export
"""

try:
    from Smart_Teaching_Assistance.ui.steps.step_input import step_input
    from Smart_Teaching_Assistance.ui.steps.step_roadmap import step_roadmap
    from Smart_Teaching_Assistance.ui.steps.step_lesson_plan import step_lesson_plan
    from Smart_Teaching_Assistance.ui.steps.step_notes import step_detailed_notes
    from Smart_Teaching_Assistance.ui.steps.step_personalize import step_personalize
    from Smart_Teaching_Assistance.ui.steps.step_export import step_export
except (ImportError, ModuleNotFoundError):
    from .step_input import step_input
    from .step_roadmap import step_roadmap
    from .step_lesson_plan import step_lesson_plan
    from .step_notes import step_detailed_notes
    from .step_personalize import step_personalize
    from .step_export import step_export

__all__ = [
    "step_input",
    "step_roadmap",
    "step_lesson_plan",
    "step_detailed_notes",
    "step_personalize",
    "step_export",
]
