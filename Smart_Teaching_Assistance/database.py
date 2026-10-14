"""
Database module for SQLite database initialization and management.
"""

import sqlite3
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class Database:
    """Manages SQLite database connections and schema initialization."""
    
    def __init__(self, db_path: str = "smart_teaching_assistant.db"):
        """
        Initialize database connection.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.connection: Optional[sqlite3.Connection] = None
        
    def connect(self) -> sqlite3.Connection:
        """
        Establish database connection.
        
        Returns:
            SQLite connection object
        """
        if self.connection is None:
            self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
        return self.connection
    
    def close(self):
        """Close database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
    
    def initialize_schema(self):
        """Create all required database tables if they don't exist."""
        conn = self.connect()
        cursor = conn.cursor()
        
        # Table: users (Multi-tenancy)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table: pdf_images
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pdf_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                pdf_hash TEXT NOT NULL,
                filename TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                element_type TEXT NOT NULL,
                bbox TEXT NOT NULL,
                image_path TEXT NOT NULL,
                caption TEXT,
                description TEXT,
                concepts TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pdf_hash 
            ON pdf_images(pdf_hash)
        """)
        
        # Table: base_notes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS base_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                topic TEXT NOT NULL,
                subtopic TEXT NOT NULL,
                difficulty_level TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL,
                pdf_hash TEXT,
                associated_images TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        # Table: personalized_notes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS personalized_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                base_notes_id INTEGER NOT NULL,
                audience_profile TEXT NOT NULL,
                content TEXT NOT NULL,
                regenerated_sections TEXT,
                associated_images TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (base_notes_id) REFERENCES base_notes(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        # Table: audience_profiles
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audience_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(user_id, name)
            )
        """)
        
        # Table: roadmaps
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS roadmaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                source_type TEXT NOT NULL,
                source_hash TEXT,
                difficulty_level TEXT NOT NULL,
                roadmap_text TEXT NOT NULL,
                roadmap_structure TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        # Table: courses (for session persistence and dashboard)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subject_name TEXT NOT NULL,
                status TEXT DEFAULT 'In Progress',
                current_step INTEGER DEFAULT 0,
                state_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # Table: settings (v1 fallback)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Table: api_keys (Multi-tenancy)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_value TEXT NOT NULL,
                user_id INTEGER,
                provider TEXT DEFAULT 'minimax',
                is_active INTEGER DEFAULT 1,
                last_used TIMESTAMP,
                error_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (key_value, user_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # Table: auth_sessions (remember login across page refreshes)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id
            ON auth_sessions(user_id)
        """)

        # Migration logic: add user_id to existing tables if missing
        tables_to_migrate = [
            "pdf_images", "base_notes", "personalized_notes", 
            "audience_profiles", "roadmaps", "courses", "api_keys"
        ]
        for table in tables_to_migrate:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
            except sqlite3.OperationalError:
                pass # Already exists
        
        conn.commit()
        logger.info(f"Database schema initialized at {self.db_path}")
    
    def execute(self, query: str, params: tuple = ()):
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor
    
    def fetchone(self, query: str, params: tuple = ()):
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()
    
    def fetchall(self, query: str, params: tuple = ()):
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()


def get_database(db_path: str = "smart_teaching_assistant.db") -> Database:
    db = Database(db_path)
    db.initialize_schema()
    return db
