# SmartTA -- PDF-Extract-Kit Integration Design

## Goal

Connect the SmartTA Streamlit app (`SmartTA_fork/`) to the PDF-Extract-Kit
pipeline (`PDF_extractor/pipeline/`) so that:

1. **Step 1 (Input Materials)** -- upload a PDF (book, paper, anything). The
   pipeline extracts **figures and diagrams only** (no formula images, no
   table images) using DocLayout-YOLO. PyMuPDF extracts text. MiniMax M3
   (with Gemini fallback) captions each figure.
2. **Steps 2-4 (Roadmap / Lesson Plan / Detailed Notes)** -- generate from
   the syllabus **and** the PDF, with section-aware RAG retrieval (text +
   image embeddings) instead of dumping the whole `pdf_text` into the prompt.
3. **Step 4-5 (Image placement)** -- the system auto-places the right PDF
   figure at the right sub-section, using CLIP similarity between the
   sub-section text and the figure (caption + raw image embedding).
4. **Step 6 (Export)** -- DOCX (always) + PDF (print-quality via ReportLab).
   Figures embedded with their original caption at the exact sub-section.

## Phases (A-F, each independently shippable) -- ALL SHIPPED

| Phase | Module(s) | Status |
|-------|-----------|--------|
| A. Ingestion | `pdf_extractor/ingest.py`, `pdf_extractor/section_detector.py`, `ui/steps/step_input.py` rewrite | ✅ done |
| B. Vector store | `rag/store.py`, `rag/embeddings.py` | ✅ done |
| C. Retrieval | `rag/retriever.py`, `rag/figure_placer.py` | ✅ done |
| D. Wire into steps 2-4 | `ui/steps/step_roadmap.py` (RAG in prompt), `step_notes.py` (per-topic RAG + figure placement) | ✅ done |
| E. Image placement logic | `rag/figure_placer.py` + harvest integration in `step_notes.py` + `figure_block` element type | ✅ done |
| F. Export | `export_manager/engine_pdf.py` + `engine_docx.py` handle `figure_block` -- figures embedded at exact sub-section with original caption | ✅ done |

## Embedding models (free, local, MIT)

- **Text**: `BAAI/bge-m3` via `sentence-transformers` -- 1.2 GB, 8K tokens,
  multilingual, MIT, runs on the GTX 1650.
- **Image**: `openai/clip-vit-base-patch32` via `sentence-transformers` --
  600 MB, 512 tokens (image patches), MIT, runs on the GTX 1650.
- **Reranker**: `BAAI/bge-reranker-v2-m3` via `sentence-transformers` --
  600 MB, MIT, runs on the GTX 1650.

Total VRAM at idle: ~2.4 GB. Your 4 GB GTX 1650 has headroom.
If VRAM gets tight at runtime, the embeddings class falls back to CPU
(1-2s/embed instead of 50ms/embed, no correctness loss).

If `gemini-embedding-2` becomes public later, swap in by changing one
import line in `rag/embeddings.py`. The retriever is model-agnostic.

## Architecture

```
Step 1 (Input)
   |
   v
[pdf_extractor/ingest.py]
   |
   |-- PyMuPDF (text per page)
   |-- DocLayout-YOLO (figure bboxes)
   |-- PyMuPDF crop (figure PNGs)
   |-- MiniMax M3 -> Gemini (caption per figure)
   |-- section_detector (TOC + heading -> tree)
   |
   v
[CourseCorpus]
   |
   v
[rag/store.py] -- Chroma persistent at courses/<hash>/chroma
   |             - text_chunks collection  (bge-m3)
   |             - figures collection      (CLIP)
   v
Step 2 (Roadmap)
   |
   v
[rag/retriever.py] -- k=8 parent-doc, section-aware, rerank
   |
   v
[step_roadmap.py] -- prompt now: "You are writing a roadmap for {topic}.
                                Use the following book excerpts and figures:
                                {retrieved_chunks + retrieved_figures}.
                                Creativity: 0.2."
   |
   v
Step 3 (Lesson Plan) -- same pattern
Step 4 (Detailed Notes) -- same pattern + figure_placer auto-inserts matching figures
Step 5 (Personalize) -- same pattern (style-only changes from learner profile)
Step 6 (Export) -- DOCX (existing) + PDF (ReportLab) with figures at sub-section
```

## Key data structures

```python
@dataclass
class Figure:
    id: str                     # 'fig-p47-c3'
    page: int
    bbox: tuple                 # (x1, y1, x2, y2)
    image_path: str             # absolute PNG path
    caption: str                # from MiniMax
    description: str            # from MiniMax (longer)
    section_id: str             # 'T3.2.1' or empty
    text_embedding: list[float] # bge-m3 of caption
    image_embedding: list[float] # CLIP of image
    content_text: str           # figure body text if any (rare)

@dataclass
class TextChunk:
    id: str                     # 'chunk-p47-para3'
    page: int
    section_id: str
    text: str
    text_embedding: list[float] # bge-m3

@dataclass
class CourseCorpus:
    pdf_hash: str
    pdf_path: str
    figures: list[Figure]
    text_chunks: list[TextChunk]
    section_tree: dict          # {"T3.2.1": {"title": "...", "page_start": 47, ...}}
```

## RAG contract (what steps 2-4 call)

```python
from Smart_Teaching_Assistance.rag.retriever import retrieve

context = retrieve(
    query="Explain backpropagation with respect to a concrete example",
    section_id="T3.2.1",         # optional, boosts section-aware chunks
    k_text=8, k_figures=3,
    rerank=True,
)
# context = {
#   "text_excerpts":  [{"id": ..., "text": ..., "page": ..., "section": ...}, ...],
#   "matching_figures": [{"id": ..., "path": ..., "caption": ..., "page": ...}, ...],
#   "source_sections": ["T3.2.1", "T3.2.3"],
# }
```

## Settings

- LLM temperature during steps 2-4: **0.2** (down from 0.4) -- user wants
  book-faithful, not creative.
- Chunk size: 512 tokens, 64 overlap.
- Per-section retrieval: 8 text chunks + 3 figures.
- Reranker top-K: 20 -> rerank to 8.
- Image-placement threshold: CLIP cosine > 0.28 (tuned empirically).

## What's explicitly NOT in v1

- URL scraping (links)
- YouTube video transcription
- Multiple PDFs per course (one PDF per session for v1)
- OCR of scanned PDFs (DocLayout-YOLO assumes text-layer PDF)
- Cross-document retrieval (one course = one PDF)
