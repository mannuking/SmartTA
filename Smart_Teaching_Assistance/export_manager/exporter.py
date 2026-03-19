"""
Export Manager for generating documents in multiple formats.
Refactored into a modular architecture with specialized engines.
"""

import json
import re
import logging
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path

from .utils import remove_tags, clean_markdown
from .engine_docx import DocxEngine
from .engine_pdf import PdfEngine
from .engine_json import JsonEngine

logger = logging.getLogger(__name__)

class ExportManager:
    """
    Facade for various export engines (DOCX, PDF, JSON).
    Delegates actual document generation to modular engines.
    """
    
    def __init__(self, db_path: str, db_connection=None):
        """Initialize export manager and its sub-engines."""
        self.db_path = db_path
        
        if db_connection:
            self.db = db_connection
        else:
            from ..database import get_database
            self.db = get_database(db_path)
        
        # Initialize engines
        self.docx_engine = DocxEngine(self.db)
        self.pdf_engine = PdfEngine(self.db)
        self.json_engine = JsonEngine(self.db)
        
        logger.info("ExportManager initialized with modular engines")

    # ── DOCX Exports ──────────────────────────────────────────────────────────

    def export_to_docx(self, content_id: str, include_tags: bool = False, output_path: str = None, content_type: str = "base") -> str:
        """Export a single note to DOCX via DocxEngine."""
        row, audience = self._fetch_note_data(content_id, content_type)
        return self.docx_engine.export_single_note(dict(row), audience, include_tags, output_path)

    def export_full_course_docx(self, subject: str, all_notes_data: List[Dict], answer_key: str = None, **kwargs) -> str:
        """Export full course to DOCX via DocxEngine."""
        return self.docx_engine.export_full_course(subject, all_notes_data, answer_key, **kwargs)

    # ── PDF Exports ───────────────────────────────────────────────────────────

    def export_to_pdf(self, content_id: str, include_tags: bool = False, output_path: str = None, content_type: str = "base") -> str:
        """Export a single note to PDF via PdfEngine."""
        row, audience = self._fetch_note_data(content_id, content_type)
        return self.pdf_engine.export_single_note(dict(row), audience, include_tags, output_path)

    def export_full_course_pdf(self, subject: str, all_notes_data: List[Dict], answer_key: str = None, **kwargs) -> str:
        """Export full course to PDF via PdfEngine."""
        return self.pdf_engine.export_full_course(subject, all_notes_data, answer_key, **kwargs)

    # ── JSON Export ───────────────────────────────────────────────────────────

    def export_to_json(self, content_id: str, output_path: str = None, content_type: str = "base") -> str:
        """Export note data to JSON via JsonEngine."""
        return self.json_engine.export(content_id, output_path, content_type)

    # ── Batch Export ──────────────────────────────────────────────────────────

    def batch_export(self, base_notes_id: str, format: str = "docx") -> List[str]:
        """Export all personalized versions of base notes."""
        rows = self.db.fetchall("SELECT id FROM personalized_notes WHERE base_notes_id = ?", (base_notes_id,))
        exported_files = []
        for row in rows:
            pid = str(row['id'])
            try:
                if format == "docx": f = self.export_to_docx(pid, content_type="personalized")
                elif format == "json": f = self.export_to_json(pid, content_type="personalized")
                elif format == "pdf": f = self.export_to_pdf(pid, content_type="personalized")
                else: continue
                exported_files.append(f)
            except Exception as e:
                logger.error(f"Error in batch export for {pid}: {e}")
        return exported_files

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _fetch_note_data(self, content_id: str, content_type: str):
        if content_type == "base":
            row = self.db.fetchone("SELECT * FROM base_notes WHERE id = ?", (content_id,))
            return row, None
        row = self.db.fetchone(
            """SELECT bn.*, pn.content, pn.audience_profile, pn.associated_images as pn_images
               FROM personalized_notes pn
               JOIN base_notes bn ON pn.base_notes_id = bn.id
               WHERE pn.id = ?""", (content_id,)
        )
        if row:
            # Override base content with personalized content
            return row, row.get('audience_profile')
        raise ValueError(f"Content {content_id} not found")

    def _get_image_metadata(self, images_json: str) -> List[Dict]:
        """Retrieve image metadata."""
        try:
            image_ids = json.loads(images_json) if isinstance(images_json, str) else images_json
            if not image_ids: return []
            images = []
            for img_id in image_ids:
                row = self.db.fetchone("SELECT * FROM pdf_images WHERE id = ?", (img_id,))
                if row: images.append(dict(row))
            return images
        except: return []
