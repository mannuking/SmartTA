"""
Smart Teaching Assistant — Streamlit Entry Point.

Run with:  streamlit run app.py
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so `Smart_Teaching_Assistance` is importable
ROOT = str(Path(__file__).resolve().parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Smart_Teaching_Assistance.ui.app import main

if __name__ == "__main__":
    main()
