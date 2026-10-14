# Smart Teaching Assistant

A comprehensive AI-powered educational content generation system with PDF extraction, audience-specific personalization, and multi-format export.

## Features

- **PDF Image Extraction**: Extract images, tables, and formulas from PDF textbooks using PyMuPDF
- **AI-Powered Content Generation**: Generate roadmaps, lessons, and notes using Google Gemini
- **Audience Personalization**: Adapt content for different audiences (e.g., Civil Engineering vs CS students)
- **Tag-Based Editing**: Fine-grained control over personalizable content sections
- **Multi-Format Export**: Export to DOCX, JSON, and PDF with embedded images
- **GPU Optimization**: Automatic GPU detection with CPU fallback
- **Streamlit UI**: Complete web interface with guided workflow

## Quick Start

### Prerequisites

- Python 3.9+
- Google Gemini API key

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd PDF_extractor

# Install dependencies (use uv; pip is not supported)
uv sync

# Set up environment variables
cp .env.example .env
# Edit .env and add your MINIMAX_API_KEY (or GEMINI_API_KEY for fallback)
```

### Running

```bash
# Start the Streamlit UI
streamlit run Smart_Teaching_Assistance/ui/app.py
```

## Project Structure

```
Smart_Teaching_Assistance/
├── __init__.py                 # Package initialization
├── config.py                   # Configuration management
├── database.py                 # SQLite database layer
├── error_handling.py           # Custom exceptions & validation
├── tag_editor.py               # Tag editing service
├── requirements.txt            # Python dependencies
│
├── pdf_extractor/              # PDF image extraction module
│   ├── __init__.py
│   └── extractor.py            # PDFImageExtractor class
│
├── content_generator/          # AI content generation module
│   ├── __init__.py
│   └── generator.py            # ContentGenerator class
│
├── personalization/            # Audience personalization module
│   ├── __init__.py
│   └── engine.py               # PersonalizationEngine class
│
├── export_manager/             # Multi-format export module
│   ├── __init__.py
│   └── exporter.py             # ExportManager class
│
├── gpu_manager/                # GPU detection & management
│   ├── __init__.py
│   └── manager.py              # GPUManager class
│
├── ui/                         # Streamlit web interface
│   ├── __init__.py
│   └── app.py                  # Main Streamlit application
│
└── tests/                      # Test suite
    ├── __init__.py
    ├── test_pdf_extractor.py
    ├── test_content_generator.py
    └── test_database.py
```

## Workflow

1. **Upload & Process**: Upload PDF textbook and/or paste syllabus text
2. **Generate Roadmap**: Create hierarchical learning roadmap from source material
3. **Create Lessons**: Generate comprehensive notes with tagged sections
4. **Edit Tags**: Fine-tune which sections are personalizable
5. **Personalize**: Generate audience-specific versions of the content
6. **Export**: Download content in DOCX, JSON, or PDF format

## Architecture

### Core Modules

| Module | Purpose |
|--------|---------|
| `PDFImageExtractor` | Extracts images from PDFs, generates descriptions using Gemini Vision, maintains extraction cache |
| `ContentGenerator` | Generates roadmaps and base notes with auto-tagging for personalizable sections |
| `PersonalizationEngine` | Creates audience profiles, regenerates tagged sections, compares versions |
| `ExportManager` | Exports to DOCX/JSON/PDF with embedded images and metadata |
| `GPUManager` | Detects CUDA GPUs, manages memory, provides CPU fallback |
| `TagEditor` | CRUD operations on content tags with edit history |

### Database Schema

| Table | Purpose |
|-------|---------|
| `pdf_images` | Extracted image metadata with descriptions and concepts |
| `base_notes` | Generated educational content with auto-tagged sections |
| `personalized_notes` | Audience-specific content versions |
| `audience_profiles` | Saved audience profiles for reuse |
| `roadmaps` | Generated learning roadmaps with hierarchical structures |
| `tag_edits` | Audit trail of tag modifications |

### Key Design Decisions

- **Tag-based personalization**: Only tagged sections (EXAMPLE, ANALOGY, APPLICATION, CASE_STUDY) are regenerated; core definitions remain untouched
- **TF-IDF image matching**: Images are semantically matched to content using TF-IDF cosine similarity
- **PDF hash caching**: Extracted images are cached by MD5 hash to avoid reprocessing
- **Cascading profile updates**: Changes to audience profiles propagate to personalized notes

## Configuration

Set these environment variables or use a `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required)* | Google Gemini API key |
| `DB_PATH` | `smart_teaching_assistant.db` | SQLite database path |
| `EXTRACTED_IMAGES_DIR` | `extracted_images` | Directory for extracted images |
| `EXPORTS_DIR` | `outputs` | Directory for exported files |
| `USE_GPU` | `true` | Enable GPU acceleration |
| `GPU_DEVICE_ID` | `0` | Preferred GPU device ID |
| `DEFAULT_DIFFICULTY` | `intermediate` | Default content difficulty |
| `MAX_IMAGES_PER_LESSON` | `5` | Maximum images per lesson |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Gemini model for text |
| `MAX_RETRIES` | `3` | API retry attempts |

## API Reference

### PDFImageExtractor

```python
extractor = PDFImageExtractor(gemini_api_key="...", db_path="my.db")

# Extract images from PDF
images = extractor.extract_images_from_pdf("textbook.pdf")

# Find relevant images for content
relevant = extractor.find_relevant_images(
    content_text="Machine learning concepts",
    pdf_hash="abc123",
    top_k=5
)
```

### ContentGenerator

```python
generator = ContentGenerator(gemini_api_key="...", db_connection=db)

# Generate and store roadmap
result = generator.generate_and_store_roadmap(
    source_text=syllabus,
    difficulty_level="intermediate"
)

# Generate base notes with tags
notes = generator.generate_base_notes(
    topic="Machine Learning",
    subtopic="Neural Networks",
    difficulty="advanced"
)

# Generate multimodal lesson
lesson = generator.generate_multimodal_lesson(
    topic="ML", subtopic="CNNs",
    difficulty="advanced",
    pdf_hash="abc123",
    pdf_extractor=extractor
)
```

### PersonalizationEngine

```python
engine = PersonalizationEngine(gemini_api_key="...", db_path="my.db")

# Create audience profile
profile_id = engine.create_audience_profile(
    "Civil Engineers",
    "Undergraduate civil engineering students"
)

# Personalize content
result = engine.personalize_content(
    base_notes_id="1",
    audience_profile="Civil engineering students"
)

# Compare versions
diff = engine.compare_versions(base_id="1", personalized_id="1")
```

### ExportManager

```python
exporter = ExportManager(db_path="my.db")

# Export to various formats
docx_path = exporter.export_to_docx(content_id="1")
json_path = exporter.export_to_json(content_id="1")
pdf_path = exporter.export_to_pdf(content_id="1")

# Batch export all personalized versions
files = exporter.batch_export(base_notes_id="1", format="docx")
```

## Running Tests

```bash
# Run all tests
pytest Smart_Teaching_Assistance/tests/ -v

# Run with coverage
pytest Smart_Teaching_Assistance/tests/ --cov=Smart_Teaching_Assistance --cov-report=html

# Run specific test module
pytest Smart_Teaching_Assistance/tests/test_pdf_extractor.py -v
```

## License

This project is part of the Smart Teaching Assistant research initiative.
