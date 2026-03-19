"""Tests for lecture-notes prompt contract wiring from lesson plan entries."""

from Smart_Teaching_Assistance.content_generator.lecture_notes import LectureNotesMixin


class DummyLectureNotesGenerator(LectureNotesMixin):
    pass


def test_create_lecture_notes_prompt_includes_section_contract_and_mega_prompt():
    gen = DummyLectureNotesGenerator()

    prompt = gen._create_lecture_notes_prompt(
        title="Backpropagation",
        plan_content="### Learning Objectives\n- Understand gradient flow",
        topic_id="T1.2",
        subject="Deep Learning",
        difficulty="advanced",
        highlights=["T1.2"],
        parent_context={"T1": "Neural Networks"},
        child_topics=None,
        previous_context="Continue from activation functions.",
        global_context="COURSE MAP: ...",
        length_budget={"min_words": 700, "max_words": 1100, "label": "thorough"},
        covered_concepts=["Perceptron", "Activation Function"],
        section_mega_prompt="Teach chain rule from intuition to matrix form.",
        coverage_contract={
            "prerequisites": ["Derivatives", "Vector notation"],
            "must_include": ["Computational graph", "Worked gradient example"],
            "must_exclude": ["Re-explain perceptron history"],
            "bridge_from_previous": "Link activation derivatives to gradient propagation.",
            "handoff_to_next": "Transition to optimization algorithms.",
        },
    )

    assert "SECTION MEGA PROMPT" in prompt
    assert "Teach chain rule from intuition to matrix form." in prompt
    assert "SECTION COVERAGE CONTRACT" in prompt
    assert "Must include in this section" in prompt
    assert "Must exclude from this section" in prompt
    assert "Re-explain perceptron history" in prompt
    assert "Anti-Redundancy" in prompt
