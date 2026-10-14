"""SmartTA command-line interface.

This is the single entry point for everything the project does. It's
installed as the `sta` console script by `pyproject.toml`, so you can
run it via `uv run sta <subcommand>` from anywhere in the project.

Subcommands:

  uv run sta              Default: start the Streamlit UI on port 8501.
  uv run sta ui           Same as above, explicit.

  uv run sta ui --port 8765 --no-browser

  uv run sta ingest <pdf_path> [--courses-dir DIR] [--device cuda|cpu]
                          Run the full PDF-Extract-Kit pipeline on a
                          PDF: text extraction + YOLO figure crops +
                          MiniMax captions + section tree + corpus save.
                          Prints a summary when done.

  uv run sta query <pdf> <topic> [--k 5] [--section T3]
                          Load a previously-ingested PDF's corpus and
                          run a RAG query. Prints text excerpts +
                          matching figures.

  uv run sta export <pdf> [--format pdf|docx] [--out path]
                          Render the in-memory corpus to a PDF or DOCX
                          (a single figure-per-page "proof of concept"
                          for now; the full export-with-lesson-plan
                          flow lives in the Streamlit UI).

  uv run sta models       Pre-download the embedding models
                          (BGE-M3 + CLIP + BGE-reranker-v2-m3) to
                          ~/.cache/huggingface/. First run only; the
                          streamlit app will use them on demand.

  uv run sta doctor       Run sanity checks: do all modules import?
                          Is .env readable? Are the embedding models
                          cached? Do CUDA / MPS / CPU work?

Examples:

  uv run sta                                          # UI
  uv run sta ingest my_textbook.pdf                   # full pipeline
  uv run sta query my_textbook.pdf "DFA construction" # RAG test
  uv run sta models                                   # warm cache
  uv run sta doctor                                   # pre-flight check
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional


# Ensure project root is on sys.path so internal imports work no
# matter how the user invokes `uv run sta`.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _resolve_pdf_path(pdf: str) -> Path:
    p = Path(pdf).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    if not p.exists():
        # Try the courses dir cache as a last resort
        cache = _ROOT / "Smart_Teaching_Assistance" / "data" / "courses"
        if cache.exists():
            for d in cache.iterdir():
                if (d / "corpus.json").exists():
                    meta = (d / "corpus.json").read_text()
                    if p.name in meta:
                        return d
        raise FileNotFoundError(
            f"PDF not found: {p}\n"
            f"Hint: run `uv run sta ingest <pdf>` first."
        )
    return p


def _ensure_courses_dir() -> Path:
    courses_dir = _ROOT / "Smart_Teaching_Assistance" / "data" / "courses"
    courses_dir.mkdir(parents=True, exist_ok=True)
    return courses_dir


def _find_cached_corpus(pdf: Path) -> Path:
    """Return the cached course dir for a given PDF (by hash)."""
    import hashlib
    h = hashlib.sha256()
    with open(pdf, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    pdf_hash = h.hexdigest()[:16]
    cache = _ensure_courses_dir() / pdf_hash
    if not (cache / "corpus.json").exists():
        raise FileNotFoundError(
            f"No cached corpus for {pdf.name} (hash {pdf_hash}).\n"
            f"Run `uv run sta ingest {pdf}` first."
        )
    return cache


# ── Subcommands ────────────────────────────────────────────────────────

def cmd_ui(args: argparse.Namespace) -> int:
    """Launch the Streamlit UI."""
    # Delegate to the streamlit runner. We spawn it as a subprocess
    # so we get the proper output streaming and Ctrl-C behavior.
    import subprocess
    app_path = _ROOT / "Smart_Teaching_Assistance" / "ui" / "app.py"
    if not app_path.exists():
        # Fallback: top-level app.py
        app_path = _ROOT / "app.py"
    if not app_path.exists():
        print(f"Error: no app.py found at {app_path}", file=sys.stderr)
        return 2

    cmd = [
        "streamlit", "run", str(app_path),
        "--server.port", str(args.port),
        "--server.headless", str(args.no_browser).lower(),
    ]
    if args.address:
        cmd += ["--server.address", args.address]
    print(f"Starting SmartTA UI on http://localhost:{args.port}")
    print(f"  app:   {app_path}")
    print(f"  run:   {' '.join(cmd)}")
    print()
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest a PDF through the full pipeline."""
    from Smart_Teaching_Assistance.pdf_extractor.ingest import ingest_pdf

    pdf = Path(args.pdf).expanduser().resolve()
    if not pdf.exists():
        print(f"Error: PDF not found: {pdf}", file=sys.stderr)
        return 1

    courses_dir = Path(args.courses_dir).expanduser().resolve() if args.courses_dir else _ensure_courses_dir()
    courses_dir.mkdir(parents=True, exist_ok=True)

    print(f"Ingesting: {pdf.name}")
    print(f"  size:   {pdf.stat().st_size / 1e6:.1f} MB")
    print(f"  device: {args.device}")
    print(f"  output: {courses_dir}")
    print()

    def _on_progress(done: int, total: int, msg: str) -> None:
        bar = "█" * (done // 5) + "░" * (20 - done // 5)
        print(f"\r  [{bar}] {done:3d}% {msg:<40s}", end="", flush=True)
        if done >= 100:
            print()

    import time
    t0 = time.time()
    corpus = ingest_pdf(
        str(pdf),
        courses_dir=str(courses_dir),
        progress_cb=_on_progress,
        force=args.force,
        device=args.device,
    )
    elapsed = time.time() - t0

    print()
    print(f"Done in {elapsed:.1f}s")
    print(f"  pages:    {corpus.total_pages}")
    print(f"  figures:  {len(corpus.figures)}")
    print(f"  chunks:   {len(corpus.text_chunks)}")
    print(f"  sections: {len(corpus.section_tree.flat_list())}")
    print(f"  hash:     {corpus.pdf_hash}")
    print()
    print("Sample figures:")
    for f in corpus.figures[:5]:
        print(f"  - p.{f.page + 1} {f.section_id or '-':3s} {f.caption[:70]!r}")
    print()
    print(f"Next: `uv run sta query {pdf.name} \"<topic>\"` to test retrieval.")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """RAG query against a previously-ingested corpus."""
    from Smart_Teaching_Assistance.pdf_extractor.corpus import CourseCorpus
    from Smart_Teaching_Assistance.rag import store
    from Smart_Teaching_Assistance.rag.retriever import retrieve

    pdf = _resolve_pdf_path(args.pdf)
    cache = _find_cached_corpus(pdf)
    print(f"Loading corpus from {cache}")
    corpus = CourseCorpus.load(str(cache.parent), cache.name)
    if corpus is None:
        print(f"Error: failed to load corpus from {cache}", file=sys.stderr)
        return 1

    print(f"  {len(corpus.figures)} figures, {len(corpus.text_chunks)} chunks")
    print()
    print("Indexing in Chroma (first run ~3 min on RTX 3070)...")
    try:
        store.index_corpus(corpus, str(cache.parent))
    except Exception as e:
        print(f"Error: index_corpus failed: {e}", file=sys.stderr)
        return 1

    print(f"Query: {args.topic!r}  (section_id={args.section!r}, k={args.k})")
    print()
    ctx = retrieve(
        corpus, str(cache.parent),
        query=args.topic,
        section_id=args.section or "",
        k_text=args.k, k_figures=max(1, args.k // 2),
        rerank=True,
    )
    print(f"  {len(ctx.text_excerpts)} text excerpts, {len(ctx.matching_figures)} figures")
    print(f"  source_sections: {ctx.source_sections}")
    print()
    for t in ctx.text_excerpts:
        page = t.page + 1 if t.page is not None else "?"
        snippet = t.text[:140].replace("\n", " ")
        print(f"  [{t.section_id or '-':3s} p.{page}] score={t.score:.2f} {snippet!r}...")
    print()
    for f in ctx.matching_figures:
        page = f.page + 1 if f.page is not None else "?"
        print(f"  fig [{f.section_id or '-':3s} p.{page}] score={f.score:.2f} {f.caption!r}")
    print(f"  image_path: {f.image_path}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Render a single-figure-per-section PDF/DOCX from a corpus.

    This is a quick proof-of-concept export for users who want to see
    what the embedded figures look like without running the full
    Streamlit flow. For the real course export, use the UI.
    """
    from Smart_Teaching_Assistance.pdf_extractor.corpus import CourseCorpus

    pdf = _resolve_pdf_path(args.pdf)
    cache = _find_cached_corpus(pdf)
    corpus = CourseCorpus.load(str(cache.parent), cache.name)
    if corpus is None:
        print(f"Error: failed to load corpus from {cache}", file=sys.stderr)
        return 1

    if args.format == "docx":
        from Smart_Teaching_Assistance.export_manager.engine_docx import DocxEngine
        from Smart_Teaching_Assistance.database import get_database
        try:
            db = get_database(":memory:")
        except Exception:
            db = None
        engine = DocxEngine(db=db)
    else:
        from Smart_Teaching_Assistance.export_manager.engine_pdf import PdfEngine
        engine = PdfEngine(db=None)

    # Build a fake "all_notes_data" tree: one node per section that
    # has at least one figure, with the figure attached as a block.
    all_notes = []
    for fig in corpus.figures[:args.limit]:
        all_notes.append({
            "id": fig.section_id or f"p{fig.page}",
            "title": f"Page {fig.page + 1} — {fig.caption[:60]}",
            "elements": [
                {
                    "type": "figure_block",
                    "image_path": str(fig.image_path),
                    "caption": fig.caption,
                    "description": fig.description,
                    "page": fig.page,
                },
                {"type": "text", "content": fig.description or "(no description)"},
            ],
        })

    out = args.out
    if not out:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in pdf.stem)
        out = _ROOT / "outputs" / f"{safe}_figures.{args.format}"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Exporting {len(all_notes)} figures to {args.format.upper()}...")
    if args.format == "docx":
        engine.export_full_course_docx(
            subject=pdf.stem,
            all_notes_data=all_notes,
            answer_key=None,
            output_path=str(out),
        )
    else:
        engine.export_full_course(
            subject=pdf.stem,
            all_notes_data=all_notes,
            answer_key=None,
            output_path=str(out),
        )
    print(f"  -> {out}  ({out.stat().st_size:,} bytes)")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """Pre-download embedding models to ~/.cache/huggingface."""
    print("Pre-downloading embedding models...")
    print("  - BAAI/bge-m3                  (~1.2 GB)")
    print("  - openai/clip-vit-base-patch32 (~600 MB)")
    print("  - BAAI/bge-reranker-v2-m3     (~600 MB)")
    print()

    import time
    t0 = time.time()
    from Smart_Teaching_Assistance.rag.embeddings import (
        get_text_embedder, get_image_embedder, get_reranker,
    )
    print(f"[{time.time() - t0:5.1f}s] loading BGE-M3...")
    m1 = get_text_embedder()
    print(f"[{time.time() - t0:5.1f}s]   dim={m1.get_embedding_dimension()}")
    print(f"[{time.time() - t0:5.1f}s] loading CLIP...")
    m2 = get_image_embedder()
    print(f"[{time.time() - t0:5.1f}s]   dim={m2.get_embedding_dimension()}")
    print(f"[{time.time() - t0:5.1f}s] loading BGE-reranker...")
    m3 = get_reranker()
    print(f"[{time.time() - t0:5.1f}s]   ok")
    print()
    print(f"All models loaded in {time.time() - t0:.1f}s.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Pre-flight sanity checks."""
    print("SmartTA doctor — running sanity checks")
    print("=" * 50)
    ok = True

    # 1. Python version
    v = sys.version_info
    print(f"  [Python]  {v.major}.{v.minor}.{v.micro} ", end="")
    if (v.major, v.minor) >= (3, 10):
        print("OK")
    else:
        print("FAIL -- need >=3.10")
        ok = False

    # 2. .env presence
    env = _ROOT / ".env"
    print(f"  [.env]    {env} ", end="")
    if env.exists():
        print("OK")
    else:
        print("MISSING (copy from .env.example)")

    # 3. Core imports
    for mod_name in [
        "streamlit", "fitz", "docx", "reportlab",
        "chromadb", "sentence_transformers", "transformers",
    ]:
        try:
            __import__(mod_name)
            print(f"  [import]  {mod_name} OK")
        except Exception as e:
            print(f"  [import]  {mod_name} FAIL: {e}")
            ok = False

    # 4. Project modules
    for mod_name in [
        "Smart_Teaching_Assistance.minimax_client",
        "Smart_Teaching_Assistance.rag.retriever",
        "Smart_Teaching_Assistance.rag.figure_placer",
        "Smart_Teaching_Assistance.pdf_extractor.ingest",
        "Smart_Teaching_Assistance.export_manager.engine_pdf",
    ]:
        try:
            __import__(mod_name)
            print(f"  [module]  {mod_name} OK")
        except Exception as e:
            print(f"  [module]  {mod_name} FAIL: {e}")
            ok = False

    # 5. CUDA
    try:
        import torch
        if torch.cuda.is_available():
            print(f"  [CUDA]    {torch.cuda.get_device_name(0)} OK")
        else:
            print("  [CUDA]    not available, will use CPU (slower YOLO)")
    except ImportError:
        print("  [CUDA]    torch not installed")

    # 6. Embedding model cache
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    if (cache / "models--BAAI--bge-m3").exists():
        print(f"  [models]  BGE-M3 cached OK")
    else:
        print(f"  [models]  BGE-M3 NOT cached -- run `uv run sta models`")

    # 7. PDF-Extract-Kit parent dir
    pek_parent = os.environ.get("PDF_EXTRACTOR_PARENT_DIR", "")
    if pek_parent:
        p = Path(pek_parent)
        if p.exists():
            print(f"  [PEK]     PDF_EXTRACTOR_PARENT_DIR={p} OK")
        else:
            print(f"  [PEK]     PDF_EXTRACTOR_PARENT_DIR={p} MISSING")
    else:
        print(f"  [PEK]     PDF_EXTRACTOR_PARENT_DIR not set -- using local YOLO fallback")

    print()
    if ok:
        print("All checks passed. Run `uv run sta` to start the UI.")
        return 0
    else:
        print("Some checks failed. Fix the issues above, then re-run.")
        return 1


# ── Argument parsing ──────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sta",
        description="Smart Teaching Assistant CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run `uv run sta <subcommand> --help` for subcommand details.",
    )
    sub = p.add_subparsers(dest="cmd")

    # ui (default if no subcommand)
    p_ui = sub.add_parser("ui", help="Start the Streamlit UI (default)")
    p_ui.add_argument("--port", type=int, default=8501, help="Port to bind (default: 8501)")
    p_ui.add_argument("--no-browser", action="store_true", help="Don't auto-open the browser")
    p_ui.add_argument("--address", default=None, help="Server address (default: 0.0.0.0)")
    p_ui.set_defaults(func=cmd_ui)

    # ingest
    p_ing = sub.add_parser("ingest", help="Ingest a PDF through the full pipeline")
    p_ing.add_argument("pdf", help="Path to the PDF file")
    p_ing.add_argument("--courses-dir", default=None, help="Override the corpus cache dir")
    p_ing.add_argument("--device", default="cuda", choices=["cuda", "cpu", "mps"], help="YOLO device (default: cuda)")
    p_ing.add_argument("--force", action="store_true", help="Re-ingest even if cached")
    p_ing.set_defaults(func=cmd_ingest)

    # query
    p_q = sub.add_parser("query", help="RAG query against a previously-ingested corpus")
    p_q.add_argument("pdf", help="PDF file (or just the filename; cache lookup)")
    p_q.add_argument("topic", help="Topic to search for")
    p_q.add_argument("--k", type=int, default=5, help="Top-K text excerpts (default: 5)")
    p_q.add_argument("--section", default="", help="Section ID to boost (e.g. T3)")
    p_q.set_defaults(func=cmd_query)

    # export
    p_e = sub.add_parser("export", help="Render a PDF/DOCX from a corpus's figures")
    p_e.add_argument("pdf", help="PDF file or corpus hash")
    p_e.add_argument("--format", default="pdf", choices=["pdf", "docx"], help="Output format (default: pdf)")
    p_e.add_argument("--out", default=None, help="Output file path")
    p_e.add_argument("--limit", type=int, default=20, help="Max figures to include (default: 20)")
    p_e.set_defaults(func=cmd_export)

    # models
    p_m = sub.add_parser("models", help="Pre-download embedding models")
    p_m.set_defaults(func=cmd_models)

    # doctor
    p_d = sub.add_parser("doctor", help="Run sanity checks")
    p_d.set_defaults(func=cmd_doctor)

    return p


def _load_dotenv() -> None:
    """Load .env from the project root if present (idempotent)."""
    try:
        from dotenv import load_dotenv
        env = _ROOT / ".env"
        if env.exists():
            load_dotenv(env, override=False)
    except ImportError:
        pass


def main(argv: Optional[list] = None) -> int:
    _load_dotenv()  # always load .env first; every subcommand benefits
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd is None:
        # No subcommand -> default to UI
        args = argparse.Namespace(
            cmd="ui", port=8501, no_browser=False, address=None,
            func=cmd_ui,
        )

    if hasattr(args, "verbose"):
        _setup_logging(args.verbose)

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
