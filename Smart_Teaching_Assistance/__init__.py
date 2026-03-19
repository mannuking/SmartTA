"""
Unified Smart Teaching Assistant
A comprehensive educational content generation system with PDF extraction,
AI-powered content generation, audience-specific personalization, and multi-format export.
"""

__version__ = "1.0.0"

from .config import Config, load_config_from_env, create_default_config
from .database import Database, get_database
from .error_handling import STAError, PDFExtractionError, ContentGenerationError, APIRateLimitError, DatabaseError, ValidationError, PauseRequested, format_error_for_ui

__all__ = [
    "Config",
    "load_config_from_env",
    "create_default_config",
    "Database",
    "get_database",
    "STAError",
    "PDFExtractionError",
    "ContentGenerationError",
    "APIRateLimitError",
    "DatabaseError",
    "ValidationError",
    "PauseRequested",
    "format_error_for_ui",
]
