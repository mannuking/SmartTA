"""
Tests for database initialization and schema.
"""

import pytest
import tempfile
import os
from pathlib import Path

from Smart_Teaching_Assistance.database import Database, get_database


def test_database_initialization():
    """Test that database initializes with correct schema."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = Database(db_path)
        db.initialize_schema()
        
        # Verify tables exist
        conn = db.connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' 
            ORDER BY name
        """)
        
        tables = [row[0] for row in cursor.fetchall()]
        
        expected_tables = [
            'audience_profiles',
            'base_notes',
            'pdf_images',
            'personalized_notes',
            'roadmaps'
        ]
        
        for table in expected_tables:
            assert table in tables, f"Table {table} not found"
        
        db.close()


def test_database_pdf_images_schema():
    """Test pdf_images table has correct columns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = get_database(db_path)
        
        conn = db.connect()
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA table_info(pdf_images)")
        columns = {row[1] for row in cursor.fetchall()}
        
        expected_columns = {
            'id', 'pdf_hash', 'filename', 'page_number', 
            'element_type', 'bbox', 'image_path', 'caption',
            'description', 'concepts', 'created_at'
        }
        
        assert expected_columns.issubset(columns)
        db.close()


def test_database_base_notes_schema():
    """Test base_notes table has correct columns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = get_database(db_path)
        
        conn = db.connect()
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA table_info(base_notes)")
        columns = {row[1] for row in cursor.fetchall()}
        
        expected_columns = {
            'id', 'topic', 'subtopic', 'difficulty_level',
            'content', 'tags', 'pdf_hash', 'associated_images',
            'created_at', 'updated_at'
        }
        
        assert expected_columns.issubset(columns)
        db.close()


def test_database_personalized_notes_schema():
    """Test personalized_notes table has correct columns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = get_database(db_path)
        
        conn = db.connect()
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA table_info(personalized_notes)")
        columns = {row[1] for row in cursor.fetchall()}
        
        expected_columns = {
            'id', 'base_notes_id', 'audience_profile', 'content',
            'regenerated_sections', 'associated_images', 'created_at'
        }
        
        assert expected_columns.issubset(columns)
        db.close()


def test_database_audience_profiles_schema():
    """Test audience_profiles table has correct columns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = get_database(db_path)
        
        conn = db.connect()
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA table_info(audience_profiles)")
        columns = {row[1] for row in cursor.fetchall()}
        
        expected_columns = {
            'id', 'name', 'description', 'created_at', 'last_used'
        }
        
        assert expected_columns.issubset(columns)
        db.close()


def test_database_roadmaps_schema():
    """Test roadmaps table has correct columns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = get_database(db_path)
        
        conn = db.connect()
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA table_info(roadmaps)")
        columns = {row[1] for row in cursor.fetchall()}
        
        expected_columns = {
            'id', 'source_type', 'source_hash', 'difficulty_level',
            'roadmap_text', 'roadmap_structure', 'created_at'
        }
        
        assert expected_columns.issubset(columns)
        db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
