"""
Tag Editor service for managing content tags.
Handles adding, removing, modifying, and moving tags within base notes content.
"""

import json
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Valid tag types
VALID_TAG_TYPES = {"EXAMPLE", "ANALOGY", "APPLICATION", "CASE_STUDY"}
TAG_PATTERN = re.compile(r'\[(EXAMPLE|ANALOGY|APPLICATION|CASE_STUDY):([^\]]+)\]')


class TagEditor:
    """Manages tag editing operations on base notes content."""
    
    def __init__(self, db):
        """
        Initialize tag editor.
        
        Args:
            db: Database instance
        """
        self.db = db
    
    def get_tags(self, base_notes_id: int) -> List[Dict]:
        """
        Get all tags for a base note.
        
        Args:
            base_notes_id: ID of base notes
            
        Returns:
            List of tag dictionaries with tag_type, context, start_position, end_position
        """
        row = self.db.fetchone(
            "SELECT content, tags FROM base_notes WHERE id = ?",
            (base_notes_id,)
        )
        if not row:
            return []
        
        tags = json.loads(row['tags'])
        return tags
    
    def add_tag(
        self,
        base_notes_id: int,
        tag_type: str,
        context: str,
        position: int
    ) -> Dict:
        """
        Add a new tag at a specific position in the content.
        
        Args:
            base_notes_id: ID of base notes
            tag_type: Type of tag (EXAMPLE, ANALOGY, APPLICATION, CASE_STUDY)
            context: Tag context/label
            position: Character position in content to insert the tag
            
        Returns:
            Updated tag information dictionary
            
        Raises:
            ValueError: If tag_type is invalid or base notes not found
        """
        if tag_type not in VALID_TAG_TYPES:
            raise ValueError(f"Invalid tag type: {tag_type}. Must be one of: {VALID_TAG_TYPES}")
        
        row = self.db.fetchone(
            "SELECT content, tags FROM base_notes WHERE id = ?",
            (base_notes_id,)
        )
        if not row:
            raise ValueError(f"Base notes not found: {base_notes_id}")
        
        content = row['content']
        tags = json.loads(row['tags'])
        
        # Build tag marker
        tag_marker = f"[{tag_type}:{context}]"
        
        # Clamp position
        position = max(0, min(position, len(content)))
        
        # Insert tag into content
        new_content = content[:position] + tag_marker + content[position:]
        
        # Recalculate all tag positions from scratch
        new_tags = self._extract_tags(new_content)
        
        # Update database
        self._update_notes(base_notes_id, new_content, new_tags)
        
        # Log edit
        self._log_edit(base_notes_id, tag_type, context, "add", old_value=None, new_value=tag_marker)
        
        logger.info(f"Added tag [{tag_type}:{context}] to notes {base_notes_id}")
        
        return {
            "tag_type": tag_type,
            "context": context,
            "position": position,
            "total_tags": len(new_tags)
        }
    
    def remove_tag(self, base_notes_id: int, tag_index: int) -> bool:
        """
        Remove a tag at a specific index.
        
        Args:
            base_notes_id: ID of base notes
            tag_index: Index of the tag in the tags list
            
        Returns:
            True if tag was removed successfully
            
        Raises:
            ValueError: If base notes not found or tag_index out of range
        """
        row = self.db.fetchone(
            "SELECT content, tags FROM base_notes WHERE id = ?",
            (base_notes_id,)
        )
        if not row:
            raise ValueError(f"Base notes not found: {base_notes_id}")
        
        content = row['content']
        tags = json.loads(row['tags'])
        
        if not tags:
            raise ValueError("No tags found in the content. Add tags before attempting to remove.")
        
        if tag_index < 0 or tag_index >= len(tags):
            raise ValueError(f"Tag index {tag_index} out of range. Valid range: 0 to {len(tags) - 1}")
        
        tag = tags[tag_index]
        start = tag['start_position']
        end = tag['end_position']
        
        # Remove tag from content
        tag_text = content[start:end]
        new_content = content[:start] + content[end:]
        
        # Recalculate all tag positions
        new_tags = self._extract_tags(new_content)
        
        # Update database
        self._update_notes(base_notes_id, new_content, new_tags)
        
        # Log edit
        self._log_edit(
            base_notes_id, tag['tag_type'], tag['context'],
            "remove", old_value=tag_text, new_value=None
        )
        
        logger.info(f"Removed tag at index {tag_index} from notes {base_notes_id}")
        return True
    
    def modify_tag(
        self,
        base_notes_id: int,
        tag_index: int,
        new_type: str = None,
        new_context: str = None
    ) -> Dict:
        """
        Modify an existing tag's type or context.
        
        Args:
            base_notes_id: ID of base notes
            tag_index: Index of the tag to modify
            new_type: New tag type (optional)
            new_context: New context/label (optional)
            
        Returns:
            Updated tag dictionary
            
        Raises:
            ValueError: If inputs are invalid
        """
        if new_type and new_type not in VALID_TAG_TYPES:
            raise ValueError(f"Invalid tag type: {new_type}. Must be one of: {VALID_TAG_TYPES}")
        
        row = self.db.fetchone(
            "SELECT content, tags FROM base_notes WHERE id = ?",
            (base_notes_id,)
        )
        if not row:
            raise ValueError(f"Base notes not found: {base_notes_id}")
        
        content = row['content']
        tags = json.loads(row['tags'])
        
        if not tags:
            raise ValueError("No tags found in the content. Add tags before attempting to modify.")
        
        if tag_index < 0 or tag_index >= len(tags):
            raise ValueError(f"Tag index {tag_index} out of range. Valid range: 0 to {len(tags) - 1}")
        
        tag = tags[tag_index]
        start = tag['start_position']
        end = tag['end_position']
        old_marker = content[start:end]
        
        # Build new tag marker
        tag_type = new_type if new_type else tag['tag_type']
        context = new_context if new_context else tag['context']
        new_marker = f"[{tag_type}:{context}]"
        
        # Replace in content
        new_content = content[:start] + new_marker + content[end:]
        
        # Recalculate tags
        new_tags = self._extract_tags(new_content)
        
        # Update database
        self._update_notes(base_notes_id, new_content, new_tags)
        
        # Log edit
        self._log_edit(
            base_notes_id, tag_type, context,
            "modify", old_value=old_marker, new_value=new_marker
        )
        
        logger.info(f"Modified tag at index {tag_index} in notes {base_notes_id}")
        
        return {"tag_type": tag_type, "context": context, "total_tags": len(new_tags)}
    
    def get_edit_history(self, base_notes_id: int) -> List[Dict]:
        """
        Get tag editing history for a base note.
        
        Args:
            base_notes_id: ID of base notes
            
        Returns:
            List of edit history dictionaries
        """
        rows = self.db.fetchall(
            """SELECT id, tag_type, tag_context, action, old_value, new_value, edited_at
               FROM tag_edits WHERE base_notes_id = ?
               ORDER BY edited_at DESC""",
            (base_notes_id,)
        )
        return [dict(row) for row in rows]
    
    def _extract_tags(self, content: str) -> List[Dict]:
        """Re-extract all tags from content."""
        tags = []
        for match in TAG_PATTERN.finditer(content):
            tags.append({
                "tag_type": match.group(1),
                "context": match.group(2),
                "start_position": match.start(),
                "end_position": match.end()
            })
        return tags
    
    def _update_notes(self, base_notes_id: int, content: str, tags: List[Dict]):
        """Update base notes content and tags in database."""
        self.db.execute(
            """UPDATE base_notes 
               SET content = ?, tags = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (content, json.dumps(tags), base_notes_id)
        )
    
    def _log_edit(
        self, base_notes_id: int, tag_type: str, context: str,
        action: str, old_value: str = None, new_value: str = None
    ):
        """Log a tag edit to the tag_edits table."""
        try:
            self.db.execute(
                """INSERT INTO tag_edits 
                   (base_notes_id, tag_type, tag_context, action, old_value, new_value)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (base_notes_id, tag_type, context, action, old_value, new_value)
            )
        except Exception as e:
            logger.warning(f"Failed to log tag edit: {e}")
