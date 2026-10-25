# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-10-25

### Added

- Ground voice descriptions in character details from original novels and scripts during import, including details omitted by script normalization.
- Generate English and Japanese voice profiles with separate voice qualities and default delivery. Add source-appropriate candidates for school-age and younger characters, early-speaking infants, and elderly characters.

### Changed

- Apply assigned voices' default delivery to previews and playback, with directions for the current line taking precedence over default pace, pauses, and emotion.
- Select multiple created voices with checkboxes, review their usages together, and delete them after confirmation. Stop on failure and retain the undeleted selections.
- Keep Character and Narrator voice assignments provisional until OK. Cancel or Close discards the changes and deletes voices generated in that dialog; generating again or assigning an existing voice first removes the unused temporary voice. Preserve pre-existing voices.
- Share the configurable `gemini.safety_threshold` in `backend/config.yaml` across script generation, analysis, and TTS GenerateContent requests. The current value is `BLOCK_NONE`; the Voice Design endpoint has no corresponding setting.

### Fixed

- Preserve offered source anchors with the same start and different ends across overlapping Import windows.
- Log individual voice lookups that return 404 at DEBUG level, while retaining warnings for list failures and permission errors.

## [0.3.3] - 2026-10-23

### Fixed

- Keep Voice Design descriptions, model, and language unchanged after a rejection. Retry only explicit temporary service failures with the same payload; do not retry timeouts or connection failures automatically.
- Show safe failure reasons and next steps in English and Japanese for individual and batch voice creation, including unsaved imports. Preserve existing and completed assignments, and retain quota and temporary-outage handling when API error context is null.

## [0.3.2] - 2026-10-21

### Fixed

- Allow Native JSON export after renaming a document. Normalize the exported Fountain title to match the document title so the result can be imported again.
- Keep the document list mounted while another document loads, preserving its scroll position and panel sizes.
- Limit scheduled Web Audio source nodes and highlight timers during long playback, replenishing them as audio finishes to prevent growing scheduling load and playback noise.

## [0.3.1] - 2026-10-21

### Added

- Add a configurable pause at speaker changes and between a spoken Scene Heading and the following speech, including narration using the same voice. The default is one second via `playback.speaker_change_pause_duration_ms` in `backend/config.yaml`; set it to zero to disable it. Other sentence boundaries and consecutive turns by the same speaker add no pause, and scene transitions keep their existing pause without adding another.
- Support Gemini 4 Argon for analysis and adaptation alongside Gemini 3.8 Flash. Select the model with `GEMINI_MODEL`; configure its output budget and timeouts in `backend/config.yaml` under `gemini.model_overrides`. Update the Google Gen AI SDK for Argon compatibility.

### Fixed

- Retry malformed or incorrectly grounded scene-verification responses within the configured attempt limit and processing deadline. Log the scene number and validation reason without source text; continue rejecting invalid evidence if attempts are exhausted.
- Allow explicitly identified non-story text windows during import while continuing to reject unexplained empty results, invalid source references, and documents without any narrative events.

## [0.3.0] - 2026-10-19

### Added

- Generate audiobook scripts with narration, character dialogue, and acting directions. For novel adaptations, scene planning and writing consider source events, character states, foreshadowing, and relevant earlier scenes.
- Automatically generate suitable acting directions for Gemini 3.8 Flash TTS's style field and inline tags for pauses and human vocal delivery.
- Check the first draft against the original source and revise affected scenes within a shared one-revision limit, then update the analysis.
- Repair combined speaker cues by rewriting only the affected passages using the original source, while preserving surrounding text.

### Changed

- Remove added fixed silence between sentences and utterances within a scene, while retaining sentence-sized TTS requests, playback from the first PCM samples, and scene-transition pauses.
- Keep persistent voice traits separate from utterance-specific acting directions and reuse assigned voices across short turns.
- Use MEDIUM thinking effort for Gemini text generation and analysis.
- Document overlapping custom voices as unsupported. Preserve existing listener and pipe notation, and reject affected playback before sending a Gemini request.

### Fixed

- Schedule received PCM buffers on the audio clock to prevent gaps caused by waiting for JavaScript end events. Stop all scheduled audio when playback is stopped.
- Scroll spoken text into view using its wrapped display position, preserve the source position when resuming within an utterance, and avoid repeated scrolling within the same utterance.
- Start unsaved or edited scripts at the cursor's sentence, including later sentences within the same utterance.
- Preserve PCM16 samples split across chunks, avoid duplicate buffer submission, and handle English abbreviations when locating a playback sentence.
- Strengthen instructions for planning labels and character names, reject generated tag-only utterances, and pass validation reasons into existing retries.
- Prevent import validation exceptions from exposing original source material or intermediate plans in logs.

### Notes

- Original source material and intermediate plans remain in memory only during import. Saved documents, Native JSON, voice assignments, and public API/event payloads retain their existing field shapes.

## [0.2.0] - 2026-10-16

### Added

- Manage created voices across documents, including assignment, usage lookup, expiry display, and deletion.
- Validate custom voice availability before saved and draft playback and preview.

## [0.1.4] - 2026-10-15

### Fixed

- Use unsaved script edits and voice assignments for playback of existing documents, so newly generated voices work before Save.

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
