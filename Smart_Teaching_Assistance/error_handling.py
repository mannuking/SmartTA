"""
Error handling and validation module for the Smart Teaching Assistant.
"""

import re
import os
import logging
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from functools import wraps

logger = logging.getLogger(__name__)


class STAError(Exception):
    """Base exception for Smart Teaching Assistant."""
    
    def __init__(self, message: str, error_code: str = "UNKNOWN", recoverable: bool = True):
        self.message = message
        self.error_code = error_code
        self.recoverable = recoverable
        super().__init__(message)


class PDFExtractionError(STAError):
    """Error during PDF extraction."""
    
    def __init__(self, message: str, recoverable: bool = True):
        super().__init__(message, "PDF_EXTRACTION", recoverable)


class ContentGenerationError(STAError):
    """Error during content generation."""
    
    def __init__(self, message: str, recoverable: bool = True):
        super().__init__(message, "CONTENT_GENERATION", recoverable)


class APIRateLimitError(STAError):
    """API rate limit exceeded."""
    
    def __init__(self, message: str = "API rate limit exceeded. Please wait a moment and try again."):
        super().__init__(message, "API_RATE_LIMIT", True)


class DatabaseError(STAError):
    """Database operation error."""
    
    def __init__(self, message: str, recoverable: bool = True):
        super().__init__(message, "DATABASE", recoverable)


class ValidationError(STAError):
    """Input validation error."""
    
    def __init__(self, message: str, field: str = ""):
        self.field = field
        super().__init__(message, "VALIDATION", True)


class PauseRequested(Exception):
    """Raised when user requested to pause generation."""
    pass


class InputValidator:
    """Validates user inputs with descriptive feedback."""
    
    # File size limits
    MAX_PDF_SIZE_MB = 100
    MAX_TEXT_LENGTH = 50000
    MIN_TEXT_LENGTH = 10
    
    # Allowed file types
    ALLOWED_EXTENSIONS = {'.pdf'}
    
    @classmethod
    def validate_pdf_file(cls, file_path: str, file_size: int = None) -> Tuple[bool, str]:
        """
        Validate PDF file upload.
        
        Args:
            file_path: Path to the uploaded file
            file_size: File size in bytes (optional)
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not file_path:
            return False, "No file selected. Please upload a PDF file."
        
        # Check extension
        ext = Path(file_path).suffix.lower()
        if ext not in cls.ALLOWED_EXTENSIONS:
            return False, f"Invalid file type '{ext}'. Only PDF files are supported."
        
        # Check file size
        if file_size is not None:
            max_bytes = cls.MAX_PDF_SIZE_MB * 1024 * 1024
            if file_size > max_bytes:
                return False, f"File is too large ({file_size / (1024*1024):.1f} MB). Maximum size is {cls.MAX_PDF_SIZE_MB} MB."
            if file_size == 0:
                return False, "The uploaded file is empty."
        
        # Check if file exists (for local files)
        if os.path.exists(file_path):
            try:
                with open(file_path, 'rb') as f:
                    header = f.read(5)
                    if header != b'%PDF-':
                        return False, "The file does not appear to be a valid PDF. It may be corrupted."
            except PermissionError:
                return False, "Cannot read the file. Check file permissions."
            except Exception as e:
                return False, f"Error reading file: {str(e)}"
        
        return True, ""
    
    @classmethod
    def validate_text_input(cls, text: str, field_name: str = "text", 
                           min_length: int = None, max_length: int = None) -> Tuple[bool, str]:
        """
        Validate text input.
        
        Args:
            text: Text to validate
            field_name: Name of the field for error messages
            min_length: Minimum length (default: MIN_TEXT_LENGTH)
            max_length: Maximum length (default: MAX_TEXT_LENGTH)
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        min_len = min_length if min_length is not None else cls.MIN_TEXT_LENGTH
        max_len = max_length if max_length is not None else cls.MAX_TEXT_LENGTH
        
        if not text or not text.strip():
            return False, f"{field_name} cannot be empty. Please enter some text."
        
        stripped = text.strip()
        if len(stripped) < min_len:
            return False, f"{field_name} is too short ({len(stripped)} chars). Minimum is {min_len} characters."
        
        if len(stripped) > max_len:
            return False, f"{field_name} is too long ({len(stripped)} chars). Maximum is {max_len} characters."
        
        return True, ""
    
    @classmethod
    def validate_audience_profile(cls, name: str, description: str) -> Tuple[bool, str]:
        """
        Validate audience profile input.
        
        Args:
            name: Profile name
            description: Profile description
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not name or not name.strip():
            return False, "Profile name cannot be empty."
        
        if len(name.strip()) < 2:
            return False, "Profile name must be at least 2 characters long."
        
        if len(name.strip()) > 100:
            return False, "Profile name must be less than 100 characters."
        
        if not description or not description.strip():
            return False, "Profile description cannot be empty."
        
        if len(description.strip()) < 5:
            return False, "Profile description must be at least 5 characters (e.g., 'Civil Engineering students')."
        
        return True, ""
    
    @classmethod
    def validate_difficulty_level(cls, level: str) -> Tuple[bool, str]:
        """Validate difficulty level."""
        valid_levels = {"beginner", "intermediate", "advanced"}
        if level.lower() not in valid_levels:
            return False, f"Invalid difficulty level '{level}'. Choose from: {', '.join(valid_levels)}"
        return True, ""


def handle_api_errors(func):
    """Decorator to handle common API errors."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_str = str(e).lower()
            
            if 'rate limit' in error_str or '429' in error_str:
                raise APIRateLimitError()
            elif 'quota' in error_str:
                raise APIRateLimitError("API quota exceeded. Please check your API plan or wait until it resets.")
            elif 'invalid api key' in error_str or '401' in error_str:
                raise ContentGenerationError(
                    "Invalid API key. Please check your MINIMAX_API_KEY (or GEMINI_API_KEY for fallback) configuration.",
                    recoverable=False
                )
            elif 'timeout' in error_str:
                raise ContentGenerationError(
                    "Request timed out. The service may be busy. Please try again.",
                    recoverable=True
                )
            elif 'connection' in error_str or 'network' in error_str:
                raise ContentGenerationError(
                    "Network error. Please check your internet connection and try again.",
                    recoverable=True
                )
            else:
                raise ContentGenerationError(f"Unexpected error: {str(e)}", recoverable=True)
    
    return wrapper


def handle_database_errors(func):
    """Decorator to handle database errors."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_str = str(e).lower()
            
            if 'disk' in error_str or 'space' in error_str:
                raise DatabaseError(
                    "Insufficient disk space. Please free up space and try again. "
                    "Consider clearing the extracted images cache.",
                    recoverable=True
                )
            elif 'locked' in error_str:
                raise DatabaseError(
                    "Database is locked. Another operation may be in progress. Please wait and try again.",
                    recoverable=True
                )
            elif 'constraint' in error_str or 'unique' in error_str:
                raise DatabaseError(
                    "A duplicate entry was detected. This item may already exist.",
                    recoverable=True
                )
            elif 'readonly' in error_str:
                raise DatabaseError(
                    "Database is in read-only mode. Check file permissions.",
                    recoverable=False
                )
            else:
                raise DatabaseError(f"Database error: {str(e)}", recoverable=True)
    
    return wrapper


class ErrorRecovery:
    """Manages error recovery to preserve existing work."""
    
    @staticmethod
    def safe_execute(operation, fallback=None, error_message="Operation failed"):
        """
        Execute an operation with error recovery.
        
        Args:
            operation: Callable to execute
            fallback: Fallback value on failure
            error_message: Message to log on failure
            
        Returns:
            Result of operation or fallback value
        """
        try:
            return operation()
        except Exception as e:
            logger.error(f"{error_message}: {e}")
            return fallback
    
    @staticmethod
    def retry_with_backoff(operation, max_retries: int = 3, base_delay: float = 1.0):
        """
        Retry an operation with exponential backoff.
        
        Args:
            operation: Callable to retry
            max_retries: Maximum number of retries
            base_delay: Base delay between retries in seconds
            
        Returns:
            Result of successful operation
            
        Raises:
            Last exception if all retries fail
        """
        import time
        
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return operation()
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Attempt {attempt + 1} failed, retrying in {delay}s: {e}")
                    time.sleep(delay)
        
        raise last_error


def format_error_for_ui(error: Exception) -> Dict:
    """
    Format an error for display in the Streamlit UI.
    
    Args:
        error: Exception to format
        
    Returns:
        Dictionary with error details for UI display
    """
    if isinstance(error, STAError):
        return {
            "type": error.error_code,
            "message": error.message,
            "recoverable": error.recoverable,
            "suggestion": _get_error_suggestion(error)
        }
    
    return {
        "type": "UNKNOWN",
        "message": str(error),
        "recoverable": True,
        "suggestion": "If the problem persists, try restarting the application."
    }


def _get_error_suggestion(error: STAError) -> str:
    """Get a user-friendly suggestion for an error."""
    suggestions = {
        "PDF_EXTRACTION": "Try uploading a different PDF file, or check if the file is corrupted.",
        "CONTENT_GENERATION": "Wait a moment and try again. If the issue persists, check your API key.",
        "API_RATE_LIMIT": (
            "You've hit the Gemini API rate limit. Don't worry — your progress is saved automatically! "
            "Wait 1-2 minutes and click 'Resume' to continue where you left off. "
            "For higher limits, consider upgrading your API plan at https://aistudio.google.com."
        ),
        "DATABASE": "Check disk space and file permissions. Your existing work is preserved.",
        "VALIDATION": "Please correct the highlighted field and try again.",
    }
    return suggestions.get(error.error_code, "Please try again or contact support.")
