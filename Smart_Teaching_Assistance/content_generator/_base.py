"""
Base mixin — core init, AI generation, caching, and shared helpers.
"""

import re
import hashlib
import logging
from collections import OrderedDict
from typing import Dict, List, Optional

# Swap Gemini SDK for our MiniMax-M3 shim. Same surface, different model.
# NOTE: minimax_client is at Smart_Teaching_Assistance/minimax_client.py,
# so we import from the package root (.. from inside content_generator/).
from .. import minimax_client as genai

from .constants import LESSON_PLAN_STRUCTURE_TAGS

logger = logging.getLogger(__name__)


class BaseMixin:
    """Core functionality shared by all generator mixins."""

    def __init__(self, gemini_api_key: str, db_connection=None, model_name: str = None, 
                 rotation_callback=None, user_id=None):
        self._api_key = gemini_api_key
        genai.configure(api_key=self._api_key)
        self._model_name = model_name or 'gemini-3-flash-preview'
        self.model = genai.GenerativeModel(self._model_name)
        self.db = db_connection
        self.user_id = user_id
        self._rotation_callback = rotation_callback
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_max = 200  # LRU eviction after this many entries
        self._request_timeout_s = 90

    def _reconfigure(self, new_key: str):
        """Update the internal API key and reconfigure the client."""
        self._api_key = new_key
        genai.configure(api_key=self._api_key)
        # Re-initialize the model with the new configuration
        self.model = genai.GenerativeModel(self._model_name)
        logger.info(f"AI Generator reconfigured with new API key: {new_key[:8]}...")

    # ── AI call with caching ─────────────────────────────────────────────────
    def _generate_text(self, prompt: str, temperature: float = 0.7,
                       purpose: str = "content", max_output_tokens: int = 8192) -> str:
        """Call Gemini with caching and automatic key rotation on rate limits."""
        cache_key = f"{purpose}_{hashlib.md5(prompt.encode()).hexdigest()}_{temperature}"
        if cache_key in self._cache:
            logger.info(f"Cache hit for {purpose}")
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        max_retries = 3
        # If we have a rotation callback, we can try more times (once per key basically)
        if self._rotation_callback:
            max_retries = 5 

        last_error = None
        for attempt in range(max_retries):
            try:
                logger.info(
                    f"Requesting AI generation for {purpose} "
                    f"(temp={temperature}, max_tokens={max_output_tokens}, attempt={attempt+1})..."
                )
                try:
                    response = self.model.generate_content(
                        prompt,
                        generation_config=genai.types.GenerationConfig(
                            temperature=temperature, max_output_tokens=max_output_tokens
                        ),
                        request_options={"timeout": self._request_timeout_s},
                    )
                except TypeError:
                    # Backward compatibility with SDK variants that don't accept request_options.
                    response = self.model.generate_content(
                        prompt,
                        generation_config=genai.types.GenerationConfig(
                            temperature=temperature, max_output_tokens=max_output_tokens
                        ),
                    )
                text = response.text.strip()
                if text:
                    self._cache[cache_key] = text
                    if len(self._cache) > self._cache_max:
                        self._cache.popitem(last=False)  # evict oldest
                    logger.info(f"AI generation for {purpose} completed. Output: {len(text)} chars.")
                else:
                    logger.warning(f"Empty response for {purpose}")
                return text

            except Exception as e:
                err_msg = str(e).lower()
                is_rate_limit = "429" in err_msg or "rate limit" in err_msg or "quota" in err_msg
                
                if is_rate_limit and self._rotation_callback:
                    logger.warning(f"Rate limit hit during {purpose}. Attempting API key rotation...")
                    new_key = self._rotation_callback()
                    if new_key:
                        self._reconfigure(new_key)
                        continue # Retry with new key
                
                logger.error(f"Error during AI generation for {purpose} (attempt {attempt+1}): {e}")
                last_error = e
                if not is_rate_limit:
                    break # Don't retry non-rate-limit errors for now to avoid loops
                
                import time
                time.sleep(2 ** attempt) # Exponential backoff for rate limits if no rotation

        raise last_error

    # ── Parsing helpers ──────────────────────────────────────────────────────
    def _parse_structured_content(self, text_content: str,
                                  tags_dict: Dict[str, str]) -> Dict[str, str]:
        """Parse [TAG_START]…[TAG_END] structured content."""
        parsed: Dict[str, str] = {}
        found_any = False
        for field_name, tag_name in tags_dict.items():
            match = re.search(
                rf"\[{tag_name}_START\](.*?)\s*\[{tag_name}_END\]",
                text_content, re.DOTALL,
            )
            if match:
                parsed[field_name] = match.group(1).strip()
                found_any = True
            else:
                parsed[field_name] = ""
        if not found_any and text_content.strip():
            parsed["raw_fallback"] = text_content.strip()
        return parsed

    def _reconstruct_markdown(self, structured: Dict[str, str]) -> str:
        """Reassemble parsed lesson plan sections into markdown."""
        if not structured or "raw_fallback" in structured:
            return structured.get("raw_fallback", "")
        ordered = [
            ("Learning Objectives", "learning_objectives"),
            ("Key Concepts & Definitions", "key_concepts"),
            ("Instructional Content Outline", "instructional_content"),
            ("Engagement Strategies & Activities", "engagement_activities"),
            ("Assessment Methods & Checkpoints", "assessment_methods"),
            ("Potential Challenges & Misconceptions", "potential_challenges"),
            ("ELI5 for Complex Concepts", "eli5_suggestion"),
            ("Educator Notes", "additional_notes"),
        ]
        parts = []
        for title, field in ordered:
            content = structured.get(field, "").strip()
            if content:
                parts.append(f"### {title}\n{content}\n")
        return "\n".join(parts).strip()

    def _find_lesson_plan_entry(self, lesson_plan: Dict, target_id: str) -> Optional[Dict]:
        """Find a node in the lesson plan tree by its ID."""
        def _search(data_list):
            for item in data_list:
                if item.get("id") == target_id:
                    return item
                for key in ("subtopics", "subsubtopics", "subsubsubtopics"):
                    if key in item and item[key]:
                        found = _search(item[key])
                        if found:
                            return found
            return None
        return _search(lesson_plan.get("topics", []))

    # ── Tree helpers ─────────────────────────────────────────────────────────
    def _count_tree_items(self, data_list: List[Dict]) -> int:
        """Count total items in a tree recursively."""
        count = 0
        for item in data_list:
            count += 1
            for key in ("subtopics", "subsubtopics", "subsubsubtopics"):
                if key in item and item[key]:
                    count += self._count_tree_items(item[key])
        return count

    def clear_cache(self):
        """Clear the generation cache."""
        self._cache.clear()
