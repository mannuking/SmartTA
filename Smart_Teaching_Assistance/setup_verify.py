"""
Setup verification script for Smart Teaching Assistance.
Verifies that all modules can be imported and database is initialized correctly.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

def verify_setup():
    """Verify the setup of Smart Teaching Assistance."""
    
    print("=" * 60)
    print("Smart Teaching Assistance - Setup Verification")
    print("=" * 60)
    print()
    
    # Test 1: Import core modules
    print("✓ Testing module imports...")
    try:
        from Smart_Teaching_Assistance import __version__
        print(f"  - Package version: {__version__}")
        
        from Smart_Teaching_Assistance.database import get_database
        print("  - Database module: OK")
        
        from Smart_Teaching_Assistance.config import Config
        print("  - Config module: OK")
        
        # Try to import other modules (may fail if dependencies not installed)
        try:
            from Smart_Teaching_Assistance.pdf_extractor import PDFImageExtractor
            print("  - PDF Extractor module: OK")
        except ImportError as e:
            print(f"  - PDF Extractor module: SKIP (missing dependency: {e})")
        
        try:
            from Smart_Teaching_Assistance.content_generator import ContentGenerator
            print("  - Content Generator module: OK")
        except ImportError as e:
            print(f"  - Content Generator module: SKIP (missing dependency: {e})")
        
        try:
            from Smart_Teaching_Assistance.personalization import PersonalizationEngine
            print("  - Personalization module: OK")
        except ImportError as e:
            print(f"  - Personalization module: SKIP (missing dependency: {e})")
        
        try:
            from Smart_Teaching_Assistance.export_manager import ExportManager
            print("  - Export Manager module: OK")
        except ImportError as e:
            print(f"  - Export Manager module: SKIP (missing dependency: {e})")
        
        print("✓ Core modules structure verified!\n")
        
    except ImportError as e:
        print(f"✗ Critical import error: {e}\n")
        return False
    
    # Test 2: Database initialization
    print("✓ Testing database initialization...")
    try:
        import tempfile
        import os
        
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_verify.db")
            db = get_database(db_path)
            
            # Check tables
            conn = db.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            expected_tables = [
                'pdf_images',
                'base_notes',
                'personalized_notes',
                'audience_profiles',
                'roadmaps'
            ]
            
            for table in expected_tables:
                if table in tables:
                    print(f"  - Table '{table}': OK")
                else:
                    print(f"  - Table '{table}': MISSING")
                    return False
            
            db.close()
        
        print("✓ Database schema initialized correctly!\n")
        
    except Exception as e:
        print(f"✗ Database error: {e}\n")
        return False
    
    # Test 3: Directory structure
    print("✓ Testing directory structure...")
    try:
        base_dir = Path(__file__).parent
        
        required_dirs = [
            'pdf_extractor',
            'content_generator',
            'personalization',
            'export_manager',
            'ui',
            'tests'
        ]
        
        for dir_name in required_dirs:
            dir_path = base_dir / dir_name
            if dir_path.exists():
                print(f"  - Directory '{dir_name}': OK")
            else:
                print(f"  - Directory '{dir_name}': MISSING")
                return False
        
        print("✓ Directory structure is correct!\n")
        
    except Exception as e:
        print(f"✗ Directory structure error: {e}\n")
        return False
    
    # Test 4: Check requirements file
    print("✓ Testing requirements file...")
    try:
        req_file = Path(__file__).parent / "requirements.txt"
        if req_file.exists():
            with open(req_file, 'r') as f:
                requirements = f.read()
                
            required_packages = [
                'streamlit',
                'google-generativeai',
                'PyMuPDF',
                'python-docx',
                'scikit-learn',
                'hypothesis',
                'pytest'
            ]
            
            for package in required_packages:
                if package in requirements:
                    print(f"  - Package '{package}': Listed")
                else:
                    print(f"  - Package '{package}': MISSING")
            
            print("✓ Requirements file exists!\n")
        else:
            print("✗ Requirements file not found!\n")
            return False
            
    except Exception as e:
        print(f"✗ Requirements file error: {e}\n")
        return False
    
    # Summary
    print("=" * 60)
    print("✓ Setup verification completed successfully!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("1. Install dependencies: pip install -r requirements.txt")
    print("2. Set up .env file with GEMINI_API_KEY")
    print("3. Run tests: pytest tests/ -v")
    print()
    
    return True


if __name__ == "__main__":
    success = verify_setup()
    sys.exit(0 if success else 1)
