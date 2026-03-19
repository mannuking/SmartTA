"""Tests for chapter-level lesson plan generation flow."""

import json

from Smart_Teaching_Assistance.content_generator.lesson_plan import LessonPlanMixin


class DummyLessonPlanGenerator(LessonPlanMixin):
    """Minimal test double for LessonPlanMixin."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def _generate_text(self, prompt, temperature=0.3, purpose="content"):
        self.calls.append({"purpose": purpose, "prompt": prompt, "temperature": temperature})
        return self.responses[purpose]

    def _count_tree_items(self, data_list):
        count = 0
        for item in data_list:
            count += 1
            for child in item.get("subtopics", []) or []:
                count += self._count_tree_items([child])
        return count

    def _parse_structured_content(self, text_content, tags_dict):
        return {k: "" for k in tags_dict.keys()}

    def _reconstruct_markdown(self, structured):
        parts = []
        for key, value in structured.items():
            if value:
                parts.append(f"### {key}\n{value}")
        return "\n\n".join(parts)


def _collect_ids(nodes):
    out = []
    for n in nodes:
        out.append(n["id"])
        out.extend(_collect_ids(n.get("subtopics", [])))
    return out


def test_generate_lesson_plan_uses_one_call_per_top_level_chapter():
    roadmap = {
        "topics": [
            {
                "id": "T1",
                "title": "Foundations",
                "subtopics": [
                    {
                        "id": "T1.1",
                        "title": "Core Concepts",
                        "subtopics": [
                            {"id": "T1.1.1", "title": "Deep Core", "subtopics": []},
                        ],
                    }
                ],
            },
            {
                "id": "T2",
                "title": "Applications",
                "subtopics": [
                    {"id": "T2.1", "title": "Case Studies", "subtopics": []},
                ],
            },
        ]
    }

    responses = {
        "lp_chapter_T1": json.dumps(
            {
                "chapter_id": "T1",
                "sections": [
                    {
                        "id": "T1",
                        "title": "Foundations",
                        "structured_content": {"learning_objectives": "Understand foundations"},
                        "teaching_prompt": "Teach chapter intro with narrative context.",
                        "coverage_contract": {"must_include": ["first principles"]},
                    },
                    {
                        "id": "T1.1",
                        "title": "Core Concepts",
                        "structured_content": {"learning_objectives": "Explain core concepts"},
                        "teaching_prompt": "Deeply teach concept mechanics.",
                        "coverage_contract": {"must_exclude": ["repeating intro"]},
                    },
                    {
                        "id": "T1.1.1",
                        "title": "Deep Core",
                        "structured_content": {"learning_objectives": "Master deep core"},
                        "teaching_prompt": "Give advanced worked examples.",
                        "coverage_contract": {"prerequisites": ["T1.1 concept map"]},
                    },
                ],
            }
        ),
        "lp_chapter_T2": json.dumps(
            {
                "chapter_id": "T2",
                "sections": [
                    {
                        "id": "T2",
                        "title": "Applications",
                        "structured_content": {"learning_objectives": "Apply theory"},
                        "teaching_prompt": "Bridge theory to practice.",
                        "coverage_contract": {"handoff_to_next": "Move to T2.1"},
                    },
                    {
                        "id": "T2.1",
                        "title": "Case Studies",
                        "structured_content": {"learning_objectives": "Solve real cases"},
                        "teaching_prompt": "Use realistic exam-style scenarios.",
                        "coverage_contract": {"must_include": ["trade-offs", "pitfalls"]},
                    },
                ],
            }
        ),
    }

    gen = DummyLessonPlanGenerator(responses)
    plan = gen.generate_lesson_plan(
        subject="ML",
        roadmap_tree=roadmap,
        difficulty_level="advanced",
        temperature=0.4,
    )

    assert [c["purpose"] for c in gen.calls] == ["lp_chapter_T1", "lp_chapter_T2"]

    generated_ids = _collect_ids(plan["topics"])
    assert generated_ids == ["T1", "T1.1", "T1.1.1", "T2", "T2.1"]

    t111 = plan["topics"][0]["subtopics"][0]["subtopics"][0]
    assert "Section Mega Prompt" in t111["content"]
    assert "advanced worked examples" in t111["content"]


def test_generate_lesson_plan_resume_skips_completed_chapter_calls():
    roadmap = {
        "topics": [
            {
                "id": "T1",
                "title": "Foundations",
                "subtopics": [
                    {"id": "T1.1", "title": "Core Concepts", "subtopics": []},
                ],
            },
            {
                "id": "T2",
                "title": "Applications",
                "subtopics": [
                    {"id": "T2.1", "title": "Case Studies", "subtopics": []},
                ],
            },
        ]
    }

    existing_plan = {
        "topics": [
            {
                "id": "T1",
                "title": "Foundations",
                "structured_content": {"learning_objectives": "Already done"},
                "content": "Existing chapter content",
                "subtopics": [
                    {
                        "id": "T1.1",
                        "title": "Core Concepts",
                        "structured_content": {"learning_objectives": "Already done sub"},
                        "content": "Existing subtopic content",
                        "subtopics": [],
                    }
                ],
            }
        ]
    }

    responses = {
        "lp_chapter_T2": json.dumps(
            {
                "chapter_id": "T2",
                "sections": [
                    {
                        "id": "T2",
                        "title": "Applications",
                        "structured_content": {"learning_objectives": "Apply"},
                        "teaching_prompt": "Teach applied patterns.",
                        "coverage_contract": {},
                    },
                    {
                        "id": "T2.1",
                        "title": "Case Studies",
                        "structured_content": {"learning_objectives": "Case mastery"},
                        "teaching_prompt": "Use full case walkthroughs.",
                        "coverage_contract": {},
                    },
                ],
            }
        )
    }

    gen = DummyLessonPlanGenerator(responses)
    plan = gen.generate_lesson_plan(
        subject="ML",
        roadmap_tree=roadmap,
        difficulty_level="advanced",
        temperature=0.4,
        existing_plan=existing_plan,
    )

    assert [c["purpose"] for c in gen.calls] == ["lp_chapter_T2"]
    assert plan["topics"][0]["content"] == "Existing chapter content"
    assert plan["topics"][0]["subtopics"][0]["content"] == "Existing subtopic content"
