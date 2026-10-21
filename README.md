# NARRAVANT

[日本語](README.ja.md)

NARRAVANT is a lightweight, local-first application that uses AI to turn novels and screenplays into [Fountain](https://fountain.io/) audiobook scripts with **narration, character dialogue, and acting directions**, then reads them aloud with Gemini TTS.

NARRAVANT plans and writes novel adaptations with the original **events, character states, and foreshadowing** in view. It also automatically generates delivery instructions for **Gemini 3.8 Flash TTS's style field and inline tags**. Existing screenplays keep their story and scene order while their narration, dialogue, and delivery notation are prepared for reading.

**One Gemini API key covers script generation, voice design, and speech.** After installing the app, add your key to the supplied local configuration and run it on your computer. The app uses SQLite for local storage; no GPU, local model download, or separate database server is required. Gemini API calls are billed separately.

Story analysis includes a synopsis, five turning points, and an emotional arc. Background music and sound effects are not supported.

## Screenshots

| Analysis workbench | Five turning points |
| --- | --- |
| ![NARRAVANT 0.3.1 in English: Oz script, emotional arc, playback controls, and character profiles](docs/images/workbench-en.png) | ![NARRAVANT 0.3.1 in English: turning point details for Oz](docs/images/turning-points-en.png) |

Sample: *The Wonderful Wizard of Oz*, Chapters 6–9, by L. Frank Baum ([Project Gutenberg, eBook #55](https://www.gutenberg.org/ebooks/55)).

### Demo

https://github.com/user-attachments/assets/857be910-ec02-4969-8bbf-928e0b68de57

> Sample: *The Wonderful Wizard of Oz*, Chapters 6–9, by L. Frank Baum. Source: [Project Gutenberg, eBook #55](https://www.gutenberg.org/ebooks/55).

## Features

- **Audiobook scripts** — Import a novel or screenplay (`.txt`, `.pdf`, `.fountain`, or `.fdx`) to prepare narration, dialogue, and acting directions in Fountain format. Re-import Native JSON to restore a work. Novel adaptation uses source events, character states, foreshadowing, and related scenes.
- **Automatic delivery instructions** — AI adds acting directions and inline tags where useful. Playback sends sustained directions through `speechMetadata.style` and keeps local tags such as `<short pause>` and `<breath>` in the spoken text supplied to Gemini 3.8 Flash TTS.
- **Lightweight local setup** — One Gemini Developer API key, a local Python service, and a browser UI. SQLite stores your work locally.
- **Story analysis** — Generate and keep a synopsis, five turning points, and an emotional arc from the uploaded work.
- **Voice design** — Generate voice traits for the narrator and each character, then create voices with Gemini and assign them per speaker. Gemini retains stateful custom voices for one year after their last use; using a voice for TTS resets this period ([Google's guide](https://ai.google.dev/gemini-api/docs/generate-content/speech-generation)).
- **Real-time playback** — Synthesize each utterance in script order with Gemini TTS and stream it over WebSocket. Generated audio is not stored; playback is regenerated each time.
- **Local-first storage** — Scripts, voice traits, voice assignments, and analysis are saved in a local database. Export and re-import via Native JSON.
- **Bilingual UI** — Switch the interface between English and Japanese.

## Tech Stack

- **Backend** — Python 3.12+, FastAPI, SQLite (with `sqlite-vec` for valence similarity search), Google Gemini (analysis, Voice Design, and TTS).
- **Frontend** — React 19, TypeScript, Vite, Tailwind CSS, i18next (English / Japanese).
- **Tooling** — `uv` for the backend, `npm` for the frontend, `make` targets for setup, run, test, and lint.

## Prerequisites

- **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/).
- **Node.js 24** (see `.nvmrc`) and `npm`.
- **`make`** (used by the setup and run commands below).
- A **Google Gemini Developer API key** with billing enabled.

## Setup and Getting Started

### 1. Environment Configuration (.env)
Copy `.env.example` to `.env` and configure the required environment variables:
```bash
cp .env.example .env
```
- `GEMINI_API_KEY`: **Required**. Your Google Gemini Developer API key.
- `GEMINI_MODEL`: Analysis and adaptation model. Supports `gemini-3.8-flash` (default) and `gemini-4-argon`.
- `GEMINI_TTS_MODEL`: Speech synthesis model (default: `gemini-3.8-flash-tts`).
- `SQLITE_DB_PATH`: Path to the local SQLite database file (default: `./runtime/narravant.sqlite3`). Relative paths are resolved from the repository root (the directory containing `.env`).
- `LOCAL_STORAGE_PATH`: Directory for local script storage (default: `./runtime/storage`). Relative paths are resolved from the repository root (the directory containing `.env`).

For the supplied local setup, keep the other defaults and replace the `GEMINI_API_KEY` placeholder. Text generation and analysis settings are in `backend/config.yaml`; `gemini.thinking_level` selects LOW, MEDIUM, or HIGH thinking effort. Restart the backend after changing these settings. This setting does not control TTS playback.

To use Gemini 4 Argon, set `GEMINI_MODEL=gemini-4-argon` in `.env`. Set it back to `gemini-3.8-flash` to use Flash. Both models use the same Gemini Developer API key; keep `GEMINI_TTS_MODEL=gemini-3.8-flash-tts` for speech synthesis.

Model-specific settings are defined in [backend/config.yaml](backend/config.yaml) under `gemini.model_overrides` and selected by `GEMINI_MODEL`. Flash uses the common stage-specific output limits and stream timeouts. The supplied Argon override sets a minimum output budget of 32,768 tokens, including thinking, and 1,200-second connection/chunk waits with a 1,200,000-millisecond HTTP timeout. The shared `processing_timeout_seconds` still limits the entire operation. Adjust these values in YAML, then restart the backend. Run `cd backend && uv sync --locked` to install the required SDK after updating.

> [!WARNING]
> **API Billing Notice**
> Calling the Gemini APIs (screenplay analysis, custom Voice Design, and TTS speech generation) will incur billable usage on your Google Cloud / Google AI Studio account. There is no in-app cost estimator or billing UI; please monitor your usage in the Google AI Studio console.

### 2. Startup (Two Processes)
Start the backend (FastAPI on port 8000) and frontend (Vite on port 5173) together:

```bash
# Initialize dependencies
make init

# Start both development servers
make run
```
Open `http://localhost:5173` in your browser.

To run each process separately, use `make dev-backend` (FastAPI on port 8000) and `make dev-frontend` (Vite on port 5173).

### 3. Basic Usage

1. **Import** a novel or screenplay (`.txt`, `.pdf`, `.fountain`, `.fdx`, or Native JSON). NARRAVANT generates a Fountain audiobook script and analyzes the story.
2. **Review the analysis** — synopsis, five turning points, and the emotional arc. Edit and save as needed.
3. **Design voices** — write voice traits for the narrator and each character, generate voices with Gemini, and assign them per speaker.
4. **Play** — once all speakers have voices, start real-time playback from the current paragraph using the current script and voice assignments, even before Save. The editor highlights and auto-scrolls to the spoken text.
5. **Export / import** via Native JSON to move a work between local databases.

Use **en / ja** to change the UI language.

## Development

- `make test` — run backend (`pytest`) and frontend (`vitest`) tests.
- `make lint` — run `ruff` (backend) and the frontend linter.
- `make clean` — remove local virtualenv, `node_modules`, build output, and the local database.

## Project Structure

```
backend/    FastAPI service: API, analysis/adaptation, Voice Design, TTS, local storage
frontend/   React + Vite web client (analysis workbench, playback, bilingual UI)
VERSION     Single source of truth for the release version
```

## Versioning

This project follows [Semantic Versioning](https://semver.org/). The release version is defined in [`VERSION`](VERSION); see [`CHANGELOG.md`](CHANGELOG.md) for release notes.

## References

See [REFERENCES.md](REFERENCES.md) for the research and technical specifications behind NARRAVANT, their use, and their limits.

## License

The software is licensed under the [MIT License](LICENSE), Copyright (c) 2026 Masayuki Irioka. It does not grant rights to imported books, third-party assets, or generated audio. You are responsible for permission to adapt and distribute the material you use.
