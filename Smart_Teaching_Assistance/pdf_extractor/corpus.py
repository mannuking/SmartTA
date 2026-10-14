"""CourseCorpus -- typed container for one PDF's extracted content.

The SmartTA app loads a single PDF per course. After `ingest.py` runs,
everything we know about that PDF lives in one CourseCorpus object:

  - text_chunks: sectioned text with embeddings, for RAG retrieval.
  - figures: cropped figures with captions, CLIP + bge-m3 embeddings.
  - section_tree: chapter/section/subsection tree built from the PDF TOC.

This object is then serialized to disk at courses/<pdf_hash>/corpus.pkl
and also indexed in Chroma (text_chunks + figures collections).

NOTE: we deliberately do NOT include extracted formulas or tables as
images. The user regenerates those as text via the LLM during roadmap
and notes generation, per the SmartTA design spec.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Figure:
    """A cropped figure (or diagram) from the PDF, plus its caption + embeddings."""

    id: str                         # e.g. 'fig-p47-c3'
    page: int                       # 0-indexed
    bbox: tuple                     # (x1, y1, x2, y2) in PIL-pixel coords at 144 dpi
    image_path: str                 # absolute path to the PNG crop
    caption: str                    # short caption from MiniMax M3
    description: str                # longer description from MiniMax M3
    section_id: str = ""            # 'T3.2.1' if the figure is inside that section
    text_embedding: list = field(default_factory=list)   # bge-m3 of caption
    image_embedding: list = field(default_factory=list)  # CLIP of image
    content_text: str = ""          # OCR'd text inside the figure (rare)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Figure":
        d = dict(d)
        d["bbox"] = tuple(d["bbox"])
        return cls(**d)


@dataclass
class TextChunk:
    """A text span from the PDF, with section tag + embedding."""

    id: str                         # e.g. 'chunk-p47-para3'
    page: int
    section_id: str = ""            # 'T3.2.1' or empty
    text: str = ""
    text_embedding: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TextChunk":
        return cls(**d)


@dataclass
class SectionTree:
    """Hierarchical chapter/section/subsection tree built from the PDF TOC.

    Format:
      {
        "T1": {"title": "Introduction", "page_start": 1, "children": {
          "T1.1": {"title": "Overview", "page_start": 3, "children": {}},
        }},
        ...
      }
    """

    nodes: dict = field(default_factory=dict)

    def flat_list(self) -> list[tuple[str, str, int]]:
        """Return [(section_id, title, page_start), ...] depth-first."""
        out: list[tuple[str, str, int]] = []

        def walk(node_id: str, node: dict):
            out.append((node_id, node.get("title", ""), node.get("page_start", 0)))
            for child_id, child in node.get("children", {}).items():
                walk(child_id, child)

        for root_id, root in self.nodes.items():
            walk(root_id, root)
        return out

    def to_dict(self) -> dict:
        return self.nodes

    @classmethod
    def from_dict(cls, d: dict) -> "SectionTree":
        return cls(nodes=d)


@dataclass
class CourseCorpus:
    """Everything we know about a single uploaded PDF."""

    pdf_hash: str
    pdf_path: str
    pdf_filename: str
    created_at: float = 0.0
    figures: list[Figure] = field(default_factory=list)
    text_chunks: list[TextChunk] = field(default_factory=list)
    section_tree: SectionTree = field(default_factory=SectionTree)
    total_pages: int = 0
    full_text: str = ""              # raw concatenated text (fallback)

    # ── Persistence ──────────────────────────────────────────────────
    def save(self, courses_dir: str | Path) -> Path:
        """Serialize to courses/<pdf_hash>/corpus.pkl (and a JSON sidecar for debugging)."""
        out_dir = Path(courses_dir) / self.pdf_hash
        out_dir.mkdir(parents=True, exist_ok=True)
        pkl_path = out_dir / "corpus.pkl"
        with open(pkl_path, "wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)
        # Also write a human-readable JSON for quick inspection
        json_path = out_dir / "corpus.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self._to_jsonable(), f, indent=2, ensure_ascii=False)
        return pkl_path

    @classmethod
    def load(cls, courses_dir: str | Path, pdf_hash: str) -> Optional["CourseCorpus"]:
        pkl_path = Path(courses_dir) / pdf_hash / "corpus.pkl"
        if not pkl_path.exists():
            return None
        with open(pkl_path, "rb") as f:
            return pickle.load(f)

    def _to_jsonable(self) -> dict:
        return {
            "pdf_hash": self.pdf_hash,
            "pdf_path": self.pdf_path,
            "pdf_filename": self.pdf_filename,
            "created_at": self.created_at,
            "total_pages": self.total_pages,
            "num_figures": len(self.figures),
            "num_text_chunks": len(self.text_chunks),
            "section_tree": self.section_tree.to_dict(),
            "figures": [
                {k: v for k, v in fig.to_dict().items() if k not in {"text_embedding", "image_embedding"}}
                for fig in self.figures
            ],
        }
