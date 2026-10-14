"""
Configuration management for API keys, paths, and GPU settings.

NOTE: This file was originally Gemini-only. It now supports a MiniMax-first
setup with a Gemini fallback.  All public field names are preserved
(`gemini_api_key`, `gemini_model`, etc.) to avoid breaking downstream
callers; the MiniMax key is read from `MINIMAX_API_KEY` (and from
`api_key_override` in the same way the Gemini key was).
"""
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Configuration settings for the Smart Teaching Assistant."""

    # API Keys (Optional here, verified at point of use)
    gemini_api_key: Optional[str] = None
    # NEW: MiniMax key (Anthropic-compatible). The text-generation path
    # uses this; the vision path also accepts it but currently falls
    # back to Gemini because the project has no working MiniMax vision
    # pipeline.
    minimax_api_key: Optional[str] = None
    # The MiniMax API base URL (Anthropic Messages endpoint). Configurable
    # so a self-hosted MiniMax-compatible gateway can be plugged in.
    minimax_base_url: str = "https://api.minimax.io/anthropic"

    # Database
    db_path: str = "smart_teaching_assistant.db"

    # File Storage Paths
    extracted_images_dir: str = "extracted_images"
    temp_uploads_dir: str = "temp_uploads"
    exports_dir: str = "outputs"

    # GPU Settings
    use_gpu: bool = True
    gpu_device_id: int = 0

    # Content Generation Settings
    default_difficulty: str = "intermediate"
    max_images_per_lesson: int = 5
    min_similarity_threshold: float = 0.3

    # API Settings
    # Field name kept as `gemini_model` for backward compatibility, but
    # the value is now the MiniMax model name (default MiniMax-M3). The
    # actual Gemini model used as fallback is configured via the
    # GEMINI_FALLBACK_MODEL env var (default `gemini-flash-lite-latest`).
    gemini_model: str = "MiniMax-M3"
    gemini_vision_model: str = "MiniMax-M3"
    max_retries: int = 3
    request_timeout: int = 60

    def __post_init__(self):
        """Create necessary directories after initialization."""
        Path(self.extracted_images_dir).mkdir(parents=True, exist_ok=True)
        Path(self.temp_uploads_dir).mkdir(parents=True, exist_ok=True)
        Path(self.exports_dir).mkdir(parents=True, exist_ok=True)


def load_config_from_env(api_key_override: Optional[str] = None,
                         minimax_key_override: Optional[str] = None,
                         user_id=None) -> Config:
    """Load configuration from environment variables (or explicit overrides).

    The `api_key_override` argument is interpreted as the **MiniMax** key
    (preserving the old "first-class API key" role, since that is now
    MiniMax). The `minimax_key_override` argument is the same thing by
    a more explicit name. `gemini_api_key` is read from `GEMINI_API_KEY`
    for the fallback path.

    Args:
        api_key_override: MiniMax API key (overrides env). Same as
            `minimax_key_override`; both are kept for compatibility.
        minimax_key_override: MiniMax API key (overrides env).
        user_id: Optional user ID to scope directories.

    Returns:
        Config object with settings from environment
    """
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    minimax_api_key = (
        minimax_key_override
        or api_key_override
        or os.getenv("MINIMAX_API_KEY")
        or os.getenv("minimax_api_key")
    )
    minimax_base_url = (
        os.getenv("MINIMAX_API_BASE")
        or os.getenv("MINIMAX_BASE_URL")
        or "https://api.minimax.io/anthropic"
    )

    if not minimax_api_key:
        logger.warning(
            "No MiniMax API key found. The app will start but all text "
            "generation will fail unless GEMINI_API_KEY is set as a fallback."
        )
    if not gemini_api_key:
        logger.info(
            "No GEMINI_API_KEY configured. The MiniMax->Gemini fallback path "
            "will be unavailable; only MiniMax will be used."
        )

    base_extracted = os.getenv("EXTRACTED_IMAGES_DIR", "extracted_images")
    base_temp = os.getenv("TEMP_UPLOADS_DIR", "temp_uploads")
    base_exports = os.getenv("EXPORTS_DIR", "outputs")

    user_suffix = f"user_{user_id}" if user_id else ""

    # Default model name is MiniMax-M3. The `gemini_model` field name is
    # kept for backward compatibility; downstream code that previously
    # created `genai.GenerativeModel(config.gemini_model)` now creates
    # our shim's `GenerativeModel` with the MiniMax name.
    model_name = os.getenv("MINIMAX_MODEL") or os.getenv("GEMINI_MODEL") or "MiniMax-M3"
    vision_model = (
        os.getenv("MINIMAX_VISION_MODEL")
        or os.getenv("GEMINI_VISION_MODEL")
        or model_name
    )

    config = Config(
        gemini_api_key=gemini_api_key,
        minimax_api_key=minimax_api_key,
        minimax_base_url=minimax_base_url,
        db_path=os.getenv("DB_PATH", "smart_teaching_assistant.db"),
        extracted_images_dir=os.path.join(base_extracted, user_suffix) if user_suffix else base_extracted,
        temp_uploads_dir=os.path.join(base_temp, user_suffix) if user_suffix else base_temp,
        exports_dir=os.path.join(base_exports, user_suffix) if user_suffix else base_exports,
        use_gpu=os.getenv("USE_GPU", "true").lower() == "true",
        gpu_device_id=int(os.getenv("GPU_DEVICE_ID", "0")),
        default_difficulty=os.getenv("DEFAULT_DIFFICULTY", "intermediate"),
        max_images_per_lesson=int(os.getenv("MAX_IMAGES_PER_LESSON", "5")),
        min_similarity_threshold=float(os.getenv("MIN_SIMILARITY_THRESHOLD", "0.3")),
        gemini_model=model_name,
        gemini_vision_model=vision_model,
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", "60")),
    )

    logger.info(
        "Configuration loaded: model=%s, base_url=%s, gemini_fallback=%s",
        config.gemini_model, config.minimax_base_url, bool(config.gemini_api_key),
    )
    return config


def create_default_config(gemini_api_key: str) -> Config:
    """Create configuration with default settings.

    Kept for backward compatibility -- the parameter is named
    `gemini_api_key` but is now treated as the primary API key
    (i.e. MiniMax). Use `create_default_config_with_keys(...)` for
    an explicit provider split.

    Args:
        gemini_api_key: Primary API key (MiniMax).
    """
    return Config(gemini_api_key=gemini_api_key, minimax_api_key=gemini_api_key)


def create_default_config_with_keys(*, minimax_api_key: str, gemini_api_key: Optional[str] = None) -> Config:
    """Create configuration with explicit MiniMax + optional Gemini fallback."""
    return Config(
        minimax_api_key=minimax_api_key,
        gemini_api_key=gemini_api_key,
    )
