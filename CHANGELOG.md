# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-10-01

### Fixed

- Restore scene navigation and audiobook playback immediately after Import, using the current unsaved script and generated voice assignments without saving a document first.
- Recreate closed audio output when restarting playback and prepare playback when the Play button is pressed.
- Match narrator voices across English and Japanese speaker names, and reconcile character cues with parenthesized aliases.
- Remove the duplicate narrator name from the Characters pane.

## [0.1.0]

The first release: turn a single work into an audiobook you can play back in real time.

### Added

- Generate an audiobook script in Fountain format (narration and dialogue) from an uploaded novel or screenplay (`.txt`, `.pdf`, `.fountain`, `.fdx`, or Native JSON).
- Generate and keep a synopsis, five turning points, and an emotional arc from the uploaded work.
- Generate voice traits for the narrator and each character, create voices with Gemini, and assign them per speaker.
- Real-time playback that synthesizes each utterance in script order with Gemini TTS and streams it over WebSocket. Generated audio is not stored and is regenerated on each playback.
- Utterance-level generation with resume from the point of failure, so works with more than two speakers play in the correct order.
- Local-first storage for scripts, voice traits, voice assignments, and analysis, with Export and re-import via Native JSON.
- Script editor with a reading player: scene-number input jumps the cursor, playback starts from the current paragraph, and the editor is read-only during playback while highlighting and auto-scrolling to the spoken text.
- Bilingual UI (English / Japanese) that translates interface text only.
- Configurable TTS retry count and inter-scene silence via `backend/config.yaml` (`playback` section).
- MIT License.

### Notes

- Gemini API keys are read from `.env` (`GEMINI_API_KEY` required). Calling the Gemini APIs incurs billable usage; there is no in-app cost estimator.
- Background music and sound effects are not supported.
