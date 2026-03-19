"""Tests for continuity prompt behavior in lecture notes generation."""

from Smart_Teaching_Assistance.content_generator.lecture_notes import LectureNotesMixin


class DummyContinuityGen(LectureNotesMixin):
    pass


def test_build_transition_context_blocks_previous_section_phrase():
    gen = DummyContinuityGen()
    ctx = gen._build_transition_context(
        prev_title="CPU Basics",
        prev_snippet="The instruction cycle coordinates fetch, decode, and execute.",
        current_title="Interrupt Handling",
    )

    assert "Do not include meta phrases" in ctx
    assert "in the previous section" in ctx


def test_section_tail_context_collects_rich_recent_content():
    gen = DummyContinuityGen()
    elements = [
        {"type": "heading", "content": "CPU and Memory"},
        {"type": "text", "content": "The CPU executes instructions while memory stores state."},
        {"type": "callout", "content": "Key Takeaway: Control signals synchronize data movement."},
        {"type": "text", "content": "At the end, interrupts alter normal control flow."},
    ]

    tail = gen._section_tail_context(elements, max_chars=240)

    assert "interrupts alter normal control flow" in tail
    assert "Control signals synchronize" in tail
