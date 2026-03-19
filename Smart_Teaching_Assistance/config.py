"""
Configuration management for API keys, paths, and GPU settings.
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
    gemini_model: str = "gemini-3-flash-preview"
    gemini_vision_model: str = "gemini-3-flash-preview"
    max_retries: int = 3
    request_timeout: int = 60
    
    def __post_init__(self):
        """Create necessary directories after initialization."""
        Path(self.extracted_images_dir).mkdir(parents=True, exist_ok=True)
        Path(self.temp_uploads_dir).mkdir(parents=True, exist_ok=True)
        Path(self.exports_dir).mkdir(parents=True, exist_ok=True)


def load_config_from_env(api_key_override: Optional[str] = None, user_id=None) -> Config:
    """
    Load configuration from environment variables or an explicit API key.
    
    Args:
        api_key_override: If provided, use this key instead of the env var.
        user_id: Optional user ID to scope directories.
    
    Returns:
        Config object with settings from environment
    """
    gemini_api_key = api_key_override or os.getenv("GEMINI_API_KEY")
    
    # We no longer raise ValueError here to allow the app to start and show the dashboard
    # even without a key. Validation happens at point of use or via has_api_key().
    if not gemini_api_key:
        logger.warning("No Gemini API key found in environment or override.")

    base_extracted = os.getenv("EXTRACTED_IMAGES_DIR", "extracted_images")
    base_temp = os.getenv("TEMP_UPLOADS_DIR", "temp_uploads")
    base_exports = os.getenv("EXPORTS_DIR", "outputs")

    user_suffix = f"user_{user_id}" if user_id else ""
    
    config = Config(
        gemini_api_key=gemini_api_key,
        db_path=os.getenv("DB_PATH", "smart_teaching_assistant.db"),
        extracted_images_dir=os.path.join(base_extracted, user_suffix) if user_suffix else base_extracted,
        temp_uploads_dir=os.path.join(base_temp, user_suffix) if user_suffix else base_temp,
        exports_dir=os.path.join(base_exports, user_suffix) if user_suffix else base_exports,
        use_gpu=os.getenv("USE_GPU", "true").lower() == "true",
        gpu_device_id=int(os.getenv("GPU_DEVICE_ID", "0")),
        default_difficulty=os.getenv("DEFAULT_DIFFICULTY", "intermediate"),
        max_images_per_lesson=int(os.getenv("MAX_IMAGES_PER_LESSON", "5")),
        min_similarity_threshold=float(os.getenv("MIN_SIMILARITY_THRESHOLD", "0.3")),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3-flash-preview"),
        gemini_vision_model=os.getenv("GEMINI_VISION_MODEL", "gemini-3-flash-preview"),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", "60")),
    )
    
    logger.info("Configuration loaded successfully")
    return config


def create_default_config(gemini_api_key: str) -> Config:
    """
    Create configuration with default settings.
    
    Args:
        gemini_api_key: Gemini API key
        
    Returns:
        Config object with default settings
    """
    return Config(gemini_api_key=gemini_api_key)
