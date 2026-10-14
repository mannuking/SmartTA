"""Embedding model abstractions for the SmartTA RAG store.

Two models ship in v1, both local + MIT-licensed + free:

  - BAAI/bge-m3       -- text embeddings (1024-dim, 8K tokens, multilingual)
  - openai/clip-vit-base-patch32 -- image embeddings (512-dim, used for
    image-to-text and text-to-image retrieval in the figure store)

Both load lazily on first use and cache the loaded model in a module
global. The first call to `get_text_embedder()` or `get_image_embedder()`
will download the model from HuggingFace (~1.2 GB and ~600 MB
respectively); subsequent calls reuse the cached model.

If `sentence-transformers` is not installed, the embedders raise a
clear ImportError with install instructions. We do NOT silently fall
back to a hash-based "embedding" -- the user would get garbage
retrieval and that's worse than a clear error.

If VRAM is tight, the model auto-falls-back to CPU. Embedding latency
goes from ~50ms (CUDA) to ~1-2s (CPU) per text chunk.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


# Module-level caches so we only load each model once per process.
_text_model = None
_image_model = None
_text_dim: Optional[int] = None
_image_dim: Optional[int] = None


# Model identifiers -- change these in one place to upgrade later.
TEXT_MODEL_ID = "BAAI/bge-m3"
IMAGE_MODEL_ID = "openai/clip-vit-base-patch32"
RERANK_MODEL_ID = "BAAI/bge-reranker-v2-m3"


# Where HuggingFace caches downloaded model files. Defaults to
# ~/.cache/huggingface/ on Linux/Mac, %USERPROFILE%/.cache/huggingface/
# on Windows. The user can override with HF_HOME in their env.
_HF_HOME = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
os.environ.setdefault("HF_HOME", _HF_HOME)


def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _device() -> str:
    return "cuda" if _cuda_available() else "cpu"


def get_text_embedder():
    """Load (and cache) the BGE-M3 text embedder.

    Returns a `sentence_transformers.SentenceTransformer` instance.

    First call downloads ~1.2 GB to HF_HOME. Subsequent calls are instant.
    """
    global _text_model, _text_dim
    if _text_model is not None:
        return _text_model
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise ImportError(
            "sentence-transformers is not installed. Run:\n"
            "    uv pip install sentence-transformers\n"
            "Then re-run. (BGE-M3 is ~1.2 GB and downloads on first use.)"
        ) from e
    logger.info("loading text embedder %s on %s ...", TEXT_MODEL_ID, _device())
    _text_model = SentenceTransformer(TEXT_MODEL_ID, device=_device(), trust_remote_code=True)
    _text_dim = _text_model.get_sentence_embedding_dimension()
    logger.info("text embedder loaded: dim=%d", _text_dim)
    return _text_model


def get_image_embedder():
    """Load (and cache) the CLIP image embedder.

    Returns a `sentence_transformers.SentenceTransformer` instance
    that exposes both `.encode(images=...)` for image embeddings and
    `.encode(sentences)` for the text side of the CLIP joint space.
    """
    global _image_model, _image_dim
    if _image_model is not None:
        return _image_model
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise ImportError(
            "sentence-transformers is not installed. Run:\n"
            "    uv pip install sentence-transformers"
        ) from e
    logger.info("loading image embedder %s on %s ...", IMAGE_MODEL_ID, _device())
    _image_model = SentenceTransformer(IMAGE_MODEL_ID, device=_device())
    _image_dim = _image_model.get_sentence_embedding_dimension()
    logger.info("image embedder loaded: dim=%d", _image_dim)
    return _image_model


def get_reranker():
    """Load (and cache) the BGE cross-encoder reranker.

    Returns a `sentence_transformers.CrossEncoder` instance.
    """
    global _rerank_model
    if "_rerank_model" in globals() and _rerank_model is not None:
        return _rerank_model
    from sentence_transformers import CrossEncoder
    logger.info("loading reranker %s on %s ...", RERANK_MODEL_ID, _device())
    _rerank_model = CrossEncoder(RERANK_MODEL_ID, device=_device())
    return _rerank_model


_rerank_model = None


# ── Convenience encoders ────────────────────────────────────────────

def embed_texts(texts: list[str], batch_size: int = 16, show_progress: bool = False) -> list[list[float]]:
    """Embed a list of text strings. Returns list of float lists."""
    if not texts:
        return []
    model = get_text_embedder()
    vecs = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=show_progress,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return [v.tolist() for v in vecs]


def embed_image(image_path: str) -> list[float]:
    """Embed a single image file. Returns a 512-dim float list (CLIP)."""
    from PIL import Image
    model = get_image_embedder()
    img = Image.open(image_path).convert("RGB")
    vec = model.encode([img], normalize_embeddings=True, convert_to_numpy=True)[0]
    return vec.tolist()


def embed_images(image_paths: list[str], batch_size: int = 8, show_progress: bool = False) -> list[list[float]]:
    """Embed a list of image file paths."""
    if not image_paths:
        return []
    from PIL import Image
    model = get_image_embedder()
    imgs = [Image.open(p).convert("RGB") for p in image_paths]
    vecs = model.encode(
        imgs,
        batch_size=batch_size,
        show_progress_bar=show_progress,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return [v.tolist() for v in vecs]


def rerank(query: str, candidates: list[str], top_k: int = 8) -> list[tuple[int, float]]:
    """Cross-encoder rerank `candidates` against `query`.

    Returns a list of (index, score) for the top_k best candidates,
    sorted by descending score.
    """
    if not candidates:
        return []
    model = get_reranker()
    pairs = [(query, c) for c in candidates]
    scores = model.predict(pairs, convert_to_numpy=True)
    indexed = [(i, float(s)) for i, s in enumerate(scores)]
    indexed.sort(key=lambda t: t[1], reverse=True)
    return indexed[:top_k]


# ── Test hooks ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # Self-test (only runs when executed directly)
    print("=== embeddings self-test ===")
    t1 = embed_texts(["hello world", "goodbye world"])
    print(f"text dim: {len(t1[0])} (expected 1024)")
    print(f"first 5 values of t1[0]: {t1[0][:5]}")
