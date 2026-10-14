"""Quick smoke test for the MiniMax client (Gemini-compat shim).

Run with:  uv run python SmartTA_fork/test_minimax_client.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `Smart_Teaching_Assistance` importable
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from Smart_Teaching_Assistance import minimax_client as genai


def test_text_only() -> None:
    """Basic text round-trip — should return a non-empty string."""
    print("=== test_text_only ===")
    if not genai.get_api_key():
        print("SKIP — MINIMAX_API_KEY not set in env")
        return
    genai.configure()  # reads from env
    model = genai.GenerativeModel("MiniMax-M3")
    response = model.generate_content(
        "In one sentence, what is a finite automaton?",
        generation_config={"temperature": 0.2, "max_output_tokens": 200},
    )
    text = response.text
    print(f"len(text) = {len(text)}")
    print(f"text      = {text[:300]}")
    assert text, "Empty response"
    print("OK")


def test_vision() -> None:
    """Vision path: send a tiny PNG + a prompt, expect a non-empty description."""
    print("=== test_vision ===")
    if not genai.get_api_key():
        print("SKIP — MINIMAX_API_KEY not set in env")
        return
    # Build a tiny synthetic PNG so the test doesn't depend on a file
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("SKIP — PIL not available")
        return
    img = Image.new("RGB", (64, 64), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((8, 8, 56, 56), outline="black", width=3)
    d.text((20, 24), "FA", fill="black")

    model = genai.GenerativeModel("MiniMax-M3")
    response = model.generate_content(
        ["Describe this image in one short sentence.", img],
        generation_config={"temperature": 0.2, "max_output_tokens": 100},
    )
    print(f"text = {response.text[:200]}")
    assert response.text, "Empty vision response"
    print("OK")


if __name__ == "__main__":
    test_text_only()
    print()
    test_vision()
