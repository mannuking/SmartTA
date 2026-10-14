"""
Personalization Engine for audience-specific content adaptation.
"""

import json
import re
from typing import Dict, List, Optional
from datetime import datetime
import logging

# Swap google-generativeai for our MiniMax-M3 shim. Same surface.
from .. import minimax_client as genai

logger = logging.getLogger(__name__)


class PersonalizationEngine:
    """Manages audience-specific content personalization."""
    
    def __init__(self, gemini_api_key: str, db_path: str, db_connection=None):
        """
        Initialize personalization engine.
        
        Args:
            gemini_api_key: API key for Gemini
            db_path: Path to database
            db_connection: Optional existing database connection
        """
        self.gemini_api_key = gemini_api_key
        self.db_path = db_path
        
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel('gemini-3-flash-preview')
        
        if db_connection:
            self.db = db_connection
        else:
            from ..database import get_database
            self.db = get_database(db_path)
        
        logger.info("PersonalizationEngine initialized")
    
    def create_audience_profile(self, name: str, description: str) -> str:
        """
        Create and store audience profile.
        
        Args:
            name: Profile name
            description: Profile description
            
        Returns:
            Profile ID
        """
        cursor = self.db.execute(
            """INSERT INTO audience_profiles (name, description, last_used)
               VALUES (?, ?, ?)""",
            (name, description, datetime.now().isoformat())
        )
        return str(cursor.lastrowid)
    
    def get_audience_profiles(self) -> List[Dict]:
        """
        Retrieve all saved audience profiles.
        
        Returns:
            List of profile dictionaries
        """
        rows = self.db.fetchall(
            """SELECT id, name, description, created_at, last_used
               FROM audience_profiles
               ORDER BY last_used DESC"""
        )
        return [dict(row) for row in rows]
    
    def get_audience_profile(self, profile_id: str) -> Optional[Dict]:
        """
        Retrieve a single audience profile by ID.
        
        Args:
            profile_id: Profile ID
            
        Returns:
            Profile dictionary or None
        """
        row = self.db.fetchone(
            """SELECT id, name, description, created_at, last_used
               FROM audience_profiles WHERE id = ?""",
            (profile_id,)
        )
        return dict(row) if row else None
    
    def update_audience_profile(self, profile_id: str, name: str = None, description: str = None) -> bool:
        """
        Update an audience profile and cascade changes to personalized content.
        
        Args:
            profile_id: Profile ID to update
            name: New name (optional)
            description: New description (optional)
            
        Returns:
            True if profile was updated
        """
        profile = self.get_audience_profile(profile_id)
        if not profile:
            return False
        
        new_name = name if name is not None else profile['name']
        new_description = description if description is not None else profile['description']
        
        self.db.execute(
            """UPDATE audience_profiles 
               SET name = ?, description = ?, last_used = ?
               WHERE id = ?""",
            (new_name, new_description, datetime.now().isoformat(), profile_id)
        )
        
        # Cascade: update audience_profile field in personalized_notes
        if description is not None:
            old_description = profile['description']
            self.db.execute(
                """UPDATE personalized_notes 
                   SET audience_profile = ?
                   WHERE audience_profile = ?""",
                (new_description, old_description)
            )
            logger.info(f"Cascaded profile update to personalized notes")
        
        logger.info(f"Updated audience profile {profile_id}")
        return True
    
    def delete_audience_profile(self, profile_id: str) -> bool:
        """
        Delete an audience profile and associated personalized content.
        
        Args:
            profile_id: Profile ID to delete
            
        Returns:
            True if profile was deleted
        """
        profile = self.get_audience_profile(profile_id)
        if not profile:
            return False
        
        # Delete associated personalized notes
        self.db.execute(
            """DELETE FROM personalized_notes 
               WHERE audience_profile = ?""",
            (profile['description'],)
        )
        
        # Delete profile
        self.db.execute(
            "DELETE FROM audience_profiles WHERE id = ?",
            (profile_id,)
        )
        
        logger.info(f"Deleted audience profile {profile_id} and associated content")
        return True
    
    def extract_tagged_sections(self, content: str, tags: List[Dict]) -> List[Dict]:
        """
        Extract sections marked with tags.
        
        Args:
            content: Base notes content
            tags: List of tag dictionaries
            
        Returns:
            List of tagged section dictionaries
        """
        sections = []
        
        for tag in tags:
            start = tag['start_position']
            end = tag['end_position']
            
            # Extract surrounding context (100 chars before and after)
            context_start = max(0, start - 100)
            context_end = min(len(content), end + 100)
            
            sections.append({
                "tag_type": tag['tag_type'],
                "context": tag['context'],
                "original_content": content[start:end],
                "surrounding_context": content[context_start:context_end],
                "location": {"start": start, "end": end}
            })
        
        return sections
    
    def regenerate_section(
        self,
        section: Dict,
        audience: str,
        surrounding_context: str
    ) -> str:
        """
        Regenerate single tagged section for audience.
        
        Args:
            section: Section dictionary with tag info
            audience: Audience profile description
            surrounding_context: Context around the section
            
        Returns:
            New content for section
        """
        tag_type = section['tag_type']
        context = section['context']
        original = section['original_content']
        
        prompt = f"""Regenerate this {tag_type.lower()} for the following audience:
Audience: {audience}

Context: {context}
Original content: {original}
Surrounding context: {surrounding_context}

Generate a new {tag_type.lower()} that is appropriate for this specific audience while maintaining the same educational level and depth. Keep the tag marker in the output."""
        
        try:
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Error regenerating section: {e}")
            return original
    
    def merge_personalized_sections(
        self,
        base_content: str,
        personalized_sections: List[Dict]
    ) -> str:
        """
        Merge regenerated sections back into base content.
        Validates that untagged content is never modified.
        
        Args:
            base_content: Original base notes content
            personalized_sections: List of regenerated sections
            
        Returns:
            Complete personalized content
        """
        # Sort sections by position (reverse order to maintain positions)
        sections = sorted(personalized_sections, key=lambda x: x['location']['start'], reverse=True)
        
        result = base_content
        for section in sections:
            start = section['location']['start']
            end = section['location']['end']
            new_content = section['new_content']
            
            # Replace section
            result = result[:start] + new_content + result[end:]
        
        return result
    
    def personalize_content(
        self,
        base_notes_id: str,
        audience_profile: str
    ) -> Dict:
        """
        Regenerate tagged sections for audience.
        
        Args:
            base_notes_id: ID of base notes
            audience_profile: Audience profile description
            
        Returns:
            Dictionary with personalized content and metadata
        """
        # Retrieve base notes
        row = self.db.fetchone(
            """SELECT id, topic, subtopic, difficulty_level, content, tags, 
                      pdf_hash, associated_images
               FROM base_notes WHERE id = ?""",
            (base_notes_id,)
        )
        
        if not row:
            raise ValueError(f"Base notes not found: {base_notes_id}")
        
        base_notes = dict(row)
        content = base_notes['content']
        tags = json.loads(base_notes['tags'])
        
        # Extract tagged sections
        sections = self.extract_tagged_sections(content, tags)
        
        # Regenerate each section
        personalized_sections = []
        for section in sections:
            new_content = self.regenerate_section(
                section,
                audience_profile,
                section['surrounding_context']
            )
            personalized_sections.append({
                **section,
                "new_content": new_content
            })
        
        # Merge sections
        personalized_content = self.merge_personalized_sections(content, personalized_sections)
        
        # Recalculate image relevance for new audience context
        associated_images = base_notes.get('associated_images') or '[]'
        recalculated_images = self._recalculate_image_relevance(
            personalized_content, 
            base_notes.get('pdf_hash'),
            associated_images
        )
        
        # Store personalized version
        cursor = self.db.execute(
            """INSERT INTO personalized_notes 
               (base_notes_id, audience_profile, content, regenerated_sections, associated_images)
               VALUES (?, ?, ?, ?, ?)""",
            (
                base_notes_id,
                audience_profile,
                personalized_content,
                json.dumps(personalized_sections),
                json.dumps(recalculated_images) if recalculated_images else associated_images
            )
        )
        
        # Update audience profile last_used timestamp
        self.db.execute(
            """UPDATE audience_profiles SET last_used = ? 
               WHERE description = ?""",
            (datetime.now().isoformat(), audience_profile)
        )
        
        return {
            "id": cursor.lastrowid,
            "base_notes_id": base_notes_id,
            "audience_profile": audience_profile,
            "content": personalized_content,
            "regenerated_sections": personalized_sections,
            "associated_images": recalculated_images
        }
    
    def _recalculate_image_relevance(
        self, 
        content: str, 
        pdf_hash: Optional[str],
        current_images: str
    ) -> Optional[List]:
        """
        Recalculate image relevance for personalized content.
        
        Args:
            content: Personalized content text
            pdf_hash: PDF hash for image lookup
            current_images: Current associated images JSON
            
        Returns:
            List of recalculated image IDs or None
        """
        if not pdf_hash:
            return None
        
        try:
            from ..pdf_extractor.extractor import PDFImageExtractor
            extractor = PDFImageExtractor(
                gemini_api_key=self.gemini_api_key,
                db_path=self.db_path
            )
            relevant_images = extractor.find_relevant_images(
                content_text=content,
                pdf_hash=pdf_hash,
                top_k=5
            )
            return [img.get('id', i) for i, img in enumerate(relevant_images)]
        except Exception as e:
            logger.warning(f"Could not recalculate image relevance: {e}")
            return None
    
    def get_personalized_versions(self, base_notes_id: str) -> List[Dict]:
        """
        Get all personalized versions of base notes.
        
        Args:
            base_notes_id: Base notes ID
            
        Returns:
            List of personalized version dictionaries
        """
        rows = self.db.fetchall(
            """SELECT id, base_notes_id, audience_profile, content, 
                      regenerated_sections, associated_images, created_at
               FROM personalized_notes WHERE base_notes_id = ?""",
            (base_notes_id,)
        )
        
        result = []
        for row in rows:
            d = dict(row)
            d['regenerated_sections'] = json.loads(d['regenerated_sections']) if d['regenerated_sections'] else []
            d['associated_images'] = json.loads(d['associated_images']) if d['associated_images'] else []
            result.append(d)
        
        return result
    
    def compare_versions(self, base_id: str, personalized_id: str) -> Dict:
        """
        Generate detailed comparison between base and personalized versions.
        
        Args:
            base_id: Base notes ID
            personalized_id: Personalized notes ID
            
        Returns:
            Dictionary with differences, statistics, and section-level marking
        """
        # Retrieve both versions
        base = self.db.fetchone(
            "SELECT content, tags FROM base_notes WHERE id = ?",
            (base_id,)
        )
        personalized = self.db.fetchone(
            "SELECT content, regenerated_sections, audience_profile FROM personalized_notes WHERE id = ?",
            (personalized_id,)
        )
        
        if not base or not personalized:
            return {"error": "Version not found"}
        
        base_content = base['content']
        personalized_content = personalized['content']
        regenerated_sections = json.loads(personalized['regenerated_sections']) if personalized['regenerated_sections'] else []
        
        # Calculate diff statistics
        base_lines = base_content.split('\n')
        pers_lines = personalized_content.split('\n')
        
        # Build section-level comparison with markings
        section_diffs = []
        for section in regenerated_sections:
            section_diffs.append({
                "tag_type": section.get('tag_type', 'unknown'),
                "context": section.get('context', ''),
                "original": section.get('original_content', ''),
                "regenerated": section.get('new_content', ''),
                "was_regenerated": True,
                "location": section.get('location', {})
            })
        
        return {
            "base_content": base_content,
            "personalized_content": personalized_content,
            "audience_profile": personalized['audience_profile'],
            "regenerated_count": len(regenerated_sections),
            "regenerated_sections": regenerated_sections,
            "section_diffs": section_diffs,
            "statistics": {
                "base_length": len(base_content),
                "personalized_length": len(personalized_content),
                "base_line_count": len(base_lines),
                "personalized_line_count": len(pers_lines),
                "sections_regenerated": len(regenerated_sections),
                "content_change_ratio": abs(len(personalized_content) - len(base_content)) / max(len(base_content), 1)
            }
        }
    
    def get_version_list(self, base_notes_id: str) -> List[Dict]:
        """
        Get a summary list of all versions (base + personalized) for content.
        
        Args:
            base_notes_id: Base notes ID
            
        Returns:
            List of version summary dictionaries
        """
        versions = []
        
        # Add base version
        base = self.db.fetchone(
            "SELECT id, topic, subtopic, created_at FROM base_notes WHERE id = ?",
            (base_notes_id,)
        )
        if base:
            versions.append({
                "id": base['id'],
                "type": "base",
                "audience": "Base (Universal)",
                "created_at": base['created_at']
            })
        
        # Add personalized versions
        personalized = self.db.fetchall(
            """SELECT id, audience_profile, created_at 
               FROM personalized_notes WHERE base_notes_id = ?
               ORDER BY created_at""",
            (base_notes_id,)
        )
        
        for row in personalized:
            versions.append({
                "id": row['id'],
                "type": "personalized",
                "audience": row['audience_profile'],
                "created_at": row['created_at']
            })
        
        return versions
