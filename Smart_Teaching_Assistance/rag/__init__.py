"""SmartTA RAG (Retrieval-Augmented Generation) module.

Layered design:

  - `embeddings.py` -- lazy-loaded BGE-M3 text + CLIP image embedders
    (sentence-transformers, both local + MIT, no API calls).
  - `store.py`      -- Chroma persistent store with two collections
    per course (text_chunks + figures). Metadata filtering for
    section_id boosts.
  - `retriever.py`  -- high-level RAG facade with cross-encoder
    reranking. The public API steps 2-4 call into.
  - `figure_placer.py` -- (Phase E) auto-places matching figures at
    the right sub-section in detailed notes via CLIP similarity.

Lazy loading throughout: nothing here is heavy at import time. The
first call to `get_text_embedder()` or `get_image_embedder()` will
download models from HuggingFace (~1.2 GB and ~600 MB respectively)
and cache them in HF_HOME (default ~/.cache/huggingface/).
"""
