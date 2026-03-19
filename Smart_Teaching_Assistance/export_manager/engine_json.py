"""
JSON generation engine for ExportManager.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

class JsonEngine:
    def __init__(self, db):
        self.db = db

    def export(
        self,
        content_id: str,
        output_path: str = None,
        content_type: str = "base"
    ) -> str:
        if content_type == "base":
            row = self.db.fetchone("SELECT * FROM base_notes WHERE id = ?", (content_id,))
        else:
            row = self.db.fetchone(
                """SELECT pn.*, bn.topic, bn.subtopic, bn.difficulty_level
                   FROM personalized_notes pn
                   JOIN base_notes bn ON pn.base_notes_id = bn.id
                   WHERE pn.id = ?""",
                (content_id,)
            )
        
        if not row:
            raise ValueError(f"Content not found: {content_id}")
        
        data = dict(row)
        for field in ['tags', 'associated_images', 'regenerated_sections', 'roadmap_structure']:
            if field in data and isinstance(data[field], str):
                try: data[field] = json.loads(data[field])
                except: pass
        
        data['export_metadata'] = {
            "exported_at": datetime.now().isoformat(),
            "content_type": content_type,
            "format": "json"
        }
        
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"export_{content_type}_{content_id}_{timestamp}.json"
            output_path = Path("outputs") / filename
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        
        return str(output_path)
