"""
Post-generation review & normalization — dedup and length-check pass.
"""

import json
import logging
from typing import Dict, List, Optional, Callable

logger = logging.getLogger(__name__)


class ReviewMixin:
    """Review and polish already-generated notes."""

    def review_and_normalize(self, all_notes: List[Dict], subject: str,
                             temperature: float = 0.3,
                             progress_cb: Optional[Callable] = None) -> List[Dict]:
        """Run a post-generation review pass to normalize lengths and remove redundancy.

        Steps:
        1. Flag sections that are too long or too short vs their target.
        2. Detect redundancy between consecutive sections.
        3. Regenerate flagged sections with explicit length/exclusion instructions.

        Returns:
            The same list with flagged sections replaced in-place.
        """
        if len(all_notes) < 2:
            return all_notes

        total = len(all_notes)

        # Pass 1: Check consecutive-section redundancy and length
        issues: List[Dict] = []
        for i in range(len(all_notes)):
            note = all_notes[i]
            word_count = sum(
                len(el.get("content", "").split())
                for el in note.get("elements", [])
                if el.get("type") in ("text", "heading", "callout")
            )
            depth = note["id"].count(".")
            has_kids = any(
                other["id"].startswith(note["id"] + ".")
                for other in all_notes if other["id"] != note["id"]
            )
            target = self._estimate_target_length(depth, has_kids)

            if word_count > target["max_words"] * 1.5:
                issues.append({"idx": i, "type": "too_long",
                               "word_count": word_count, "target": target})
            if i < len(all_notes) - 1:
                current_text = " ".join(
                    el.get("content", "") for el in note.get("elements", [])
                    if el.get("type") == "text"
                )[:500]
                next_text = " ".join(
                    el.get("content", "") for el in all_notes[i + 1].get("elements", [])
                    if el.get("type") == "text"
                )[:500]
                if current_text and next_text:
                    curr_words = set(current_text.lower().split())
                    next_words = set(next_text.lower().split())
                    if len(curr_words) > 10 and len(next_words) > 10:
                        overlap = len(curr_words & next_words) / min(len(curr_words), len(next_words))
                        if overlap > 0.45:
                            issues.append({"idx": i + 1, "type": "redundant",
                                           "overlap": overlap,
                                           "prev_id": note["id"]})

        if not issues:
            logger.info("Review pass: no issues found.")
            return all_notes

        logger.info(f"Review pass: {len(issues)} issues found. Fixing...")

        for issue_num, issue in enumerate(issues):
            idx = issue["idx"]
            note = all_notes[idx]
            if progress_cb:
                progress_cb(issue_num + 1, len(issues),
                            f"Polishing: {note['id']} — {note['title']}")

            if issue["type"] == "too_long":
                target = issue["target"]
                prompt = (
                    f"The following section is {issue['word_count']} words but should be "
                    f"{target['label']}. Condense it while preserving all key facts, "
                    f"definitions, and examples. Remove redundancy and fluff.\n\n"
                    f"Topic: {note['id']}: {note['title']}\n"
                    f"Subject: {subject}\n\n"
                    f"Current content:\n"
                )
                for el in note.get("elements", []):
                    if el.get("type") == "text":
                        prompt += el["content"] + "\n"
                prompt += (
                    f"\nOutput ONLY valid JSON with the same element structure. "
                    f"Target: {target['min_words']}-{target['max_words']} words.\n"
                    f'{{"elements": [...]}}'
                )
                try:
                    raw = self._generate_text(prompt, temperature, f"review_condense_{note['id']}")
                    cleaned = raw.strip()
                    if cleaned.startswith("```json"): cleaned = cleaned[7:]
                    elif cleaned.startswith("```"): cleaned = cleaned[3:]
                    if cleaned.endswith("```"): cleaned = cleaned[:-3]
                    data = json.loads(cleaned.strip())
                    new_elements = data.get("elements", [])
                    if new_elements:
                        note["elements"] = new_elements
                        logger.info(f"Condensed {note['id']} from {issue['word_count']}w to target")
                except Exception as e:
                    logger.warning(f"Failed to condense {note['id']}: {e}")

            elif issue["type"] == "redundant":
                prev_note = all_notes[issue["idx"] - 1] if issue["idx"] > 0 else None
                if prev_note:
                    prev_content = " ".join(
                        el.get("content", "") for el in prev_note.get("elements", [])
                        if el.get("type") == "text"
                    )[:800]
                    prompt = (
                        f"The following section has significant content overlap with the "
                        f"preceding section \"{prev_note['id']}: {prev_note['title']}\". "
                        f"Rewrite THIS section to eliminate all repeated definitions, "
                        f"concepts, and examples. Reference the prior section instead of "
                        f"re-explaining.\n\n"
                        f"PRECEDING SECTION summary:\n{prev_content}\n\n"
                        f"THIS SECTION to de-duplicate:\n"
                        f"Topic: {note['id']}: {note['title']}\n"
                    )
                    for el in note.get("elements", []):
                        if el.get("type") == "text":
                            prompt += el["content"] + "\n"
                    prompt += (
                        f'\nOutput ONLY valid JSON: {{"elements": [...]}}'
                    )
                    try:
                        raw = self._generate_text(prompt, temperature, f"review_dedup_{note['id']}")
                        cleaned = raw.strip()
                        if cleaned.startswith("```json"): cleaned = cleaned[7:]
                        elif cleaned.startswith("```"): cleaned = cleaned[3:]
                        if cleaned.endswith("```"): cleaned = cleaned[:-3]
                        data = json.loads(cleaned.strip())
                        new_elements = data.get("elements", [])
                        if new_elements:
                            note["elements"] = new_elements
                            logger.info(f"De-duplicated {note['id']} (overlap={issue['overlap']:.0%})")
                    except Exception as e:
                        logger.warning(f"Failed to de-duplicate {note['id']}: {e}")

        return all_notes
