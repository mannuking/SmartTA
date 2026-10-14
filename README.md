# Smart Teaching Assistant (MiniMax-M3 fork)

An AI-powered teaching assistant that transforms your syllabus into complete, structured teaching materials — lesson plans, lecture notes, flashcards, and more.

Built with **Streamlit** and **MiniMax M3** (Anthropic-compatible API) — with **automatic fallback to Google Gemini** when MiniMax returns a 402 (insufficient balance), 429 (rate limit), or 5xx.

![Python](https://img.shields.io/badge/python-3.9+-blue.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-1.28+-red)
![Provider](https://img.shields.io/badge/MiniMax-M3-orange)
![License](https://img.shields.io/badge/License-Apache%202.0-green)

## About this fork

This is a fork of [jay1331k/SmartTA](https://github.com/jay1331k/SmartTA), patched to use **MiniMax M3** as the primary LLM and **Gemini as the fallback**. The fork is maintained inside the same project as a side-app — it has its own dependency tree and is loaded by `streamlit run SmartTA_fork/app.py`.

The fork keeps the upstream code's call shape: every `genai.configure(...)`, `genai.GenerativeModel(name)`, and `model.generate_content(prompt, ...)` call still works — they're transparently routed to MiniMax M3 (or Gemini on fallback) by `Smart_Teaching_Assistance/minimax_client.py`.

## Features

- **Smart Input**: Paste your syllabus or upload a textbook PDF for richer AI context
- **Learning Roadmap**: Auto-generates hierarchical topic trees from your syllabus
- **Lesson Plans**: Creates structured pedagogical plans with objectives, activities, and assessments
- **Detailed Notes**: Generates rich lecture notes with diagrams, examples, and key concepts
- **Audience Personalization**: Tailor notes for different student groups (e.g., beginners vs advanced)
- **Multi-format Export**: Download as Markdown, DOCX, or PDF
- **Pause & Resume**: Long generations can be paused and resumed — progress is auto-saved
- **Auto-Save**: All work is persisted to a local SQLite database

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

If you use the project conda env (e.g. `pdf-extractor`):

```bash
conda activate pdf-extractor
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. Configure your API keys

Create a `.env` file in the project root (one-time) with at least one of:

```env
# Primary (MiniMax M3)
MINIMAX_API_KEY=your_minimax_api_key_here
MINIMAX_API_BASE=https://api.minimax.io/anthropic
MINIMAX_MODEL=MiniMax-M3

# Fallback (Google Gemini)
GEMINI_API_KEY=your_gemini_api_key_here
```

Either key is enough. If `MINIMAX_API_KEY` is set, the app uses MiniMax M3 by default and only falls back to Gemini when MiniMax returns 402/429/5xx. If only `GEMINI_API_KEY` is set, the app uses Gemini for everything (the old upstream behavior).

Get a Gemini key at https://aistudio.google.com/apikey (free).

### 3. Run the app

```bash
streamlit run app.py
```

### 4. (Optional) Override in-app

You can also enter your **MiniMax API key** in the sidebar.

## Project Structure

```
SmartTA/
├── app.py                          # Streamlit entry point
├── requirements.txt                # Python dependencies
├── README.md
├── .gitignore
└── Smart_Teaching_Assistance/      # Core package
    ├── __init__.py
    ├── config.py                   # Configuration management
    ├── database.py                 # SQLite persistence layer
    ├── error_handling.py           # Error types & recovery
    ├── tag_editor.py               # Tag-based personalization
    ├── content_generator/          # AI content generation (mixin-based)
    │   ├── generator.py            # Main ContentGenerator class
    │   ├── roadmap.py              # Roadmap generation
    │   ├── lesson_plan.py          # Lesson plan generation
    │   ├── lecture_notes.py        # Detailed notes generation
    │   ├── review.py               # Summaries & flashcards
    │   ├── study_tools.py          # Question banks
    │   ├── image_downloader.py     # Diagram/image fetching
    │   └── ...
    ├── pdf_extractor/              # PDF text & image extraction
    ├── personalization/            # Audience-specific adaptation
    ├── export_manager/             # Multi-format export
    ├── gpu_manager/                # Optional GPU support
    └── ui/                         # Streamlit UI layer
        ├── app.py                  # Main Streamlit app logic
        ├── state_manager.py        # Session state management
        ├── background_worker.py    # Threaded generation workers
        ├── components/             # Reusable UI components
        │   ├── dashboard.py        # Dashboard with usage guide
        │   ├── sidebar.py          # Sidebar with API key input
        │   └── common.py           # CSS, progress bars
        └── steps/                  # 6-step workflow
            ├── step_input.py
            ├── step_roadmap.py
            ├── step_lesson_plan.py
            ├── step_notes.py
            ├── step_personalize.py
            └── step_export.py
```

## How It Works

1. **Input** your subject name and syllabus (paste text or upload PDF)
2. **Generate a Roadmap** — the AI organizes your syllabus into a hierarchical topic tree
3. **Create Lesson Plans** — structured teaching plans for every topic
4. **Generate Notes** — detailed lecture notes with images, examples, and concepts
5. **Personalize** — adapt content for different audience profiles
6. **Export** — download everything in your preferred format

## Rate Limits

The free Gemini API tier has usage limits. If you hit them:
- Your progress is **automatically saved**
- Wait 1-2 minutes and click **Resume**
- For higher limits, upgrade at [Google AI Studio](https://aistudio.google.com)

## Deploy on Streamlit Community Cloud

1. Push this repo to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) and click **New app**.
3. Select:
    - Repository: your `SmartTA` repo
    - Branch: your deploy branch
    - Main file path: `app.py`
4. Deploy.

### Deployment Notes

- This repo includes `runtime.txt` pinned to Python 3.11 for cloud stability.
- Dependencies are read from root `requirements.txt`.
- API keys can be entered in-app from the dashboard. You can also set `GEMINI_API_KEY` in app secrets/env if preferred.

### Important Persistence Note

This app currently uses local SQLite (`smart_teaching_assistant.db`). On Streamlit Cloud, local filesystem data can be reset when the app restarts/redeploys.

For durable multi-user production data, use a managed external database (e.g. Postgres/Supabase) and move state there.

## License

MIT
