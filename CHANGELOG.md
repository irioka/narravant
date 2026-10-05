# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - 2026-10-05

### Added

- Allow resizing and restoring the document list's Version and Saved at columns.
- Add and remove Characters while keeping turning point records and unrelated arc series intact.
- Read Scene Headings with the narrator voice, omitting location prefixes and scene numbers.
- Show a loading indicator while waiting for the first playback audio.
- Split long TTS utterances into sentence-sized requests and add a configurable one-second pause between same-scene segments and utterances.

### Fixed

- Preserve analysis history when character names are absent from the current script or Characters list.
- Use Fountain speakers as the source for Emotional Arc reanalysis and add missing speaker profiles when applying results.
- Reanalyze the current unsaved script, including Import drafts, and save script and arc changes together.
- Allow another Import before saving and disable Delete for unsaved Native JSON drafts.
- Keep document-list resize handles moving in the drag direction.
- Allow backend packaging and startup without a backend-specific README.

## [0.1.2] - 2026-10-04

### Fixed

- Keep the source language throughout AI import and reject clearly mismatched narration or analysis, including short descriptive sentences.
- Validate generated speaker cues before analysis to prevent action descriptions from becoming character names, while retaining valid names and CONT'D speaker identity.
- Request freshly worded prose adaptations and reinforce that instruction within existing retries after RECITATION, without accepting blocked output.
- Regenerate scenes with empty or malformed speaker cues within the existing shared scene budget, and report safe scene and line positions on failure.

### Changed

- Simplify the English/Japanese UI language-switch explanation in Basic Usage.

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
