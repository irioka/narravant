# README images

Public screenshots referenced by the top-level `README.md` / `README.ja.md`
live here. The UI is captured in Japanese; the same images are shared by both
READMEs.

| File | Screen | Status |
| --- | --- | --- |
| `workbench.png` | Analysis workbench: document list, script editor with the playback bar, and the character/voice pane | present |
| `turning-points.png` | The five turning points (turning point dialog) | present |

## Demo video

The demo is a ~3 minute screen recording (import → analysis → voice design →
real-time playback with auto-scroll). It is **not** committed to the repo
(GitHub does not play repo-committed videos, and `*.mp4` is git-ignored).

- Compressed file for upload: `~/Videos/narravant-demo-720p.mp4`
  (720p, ~7.7 MB, under GitHub's 10 MB attachment limit).
- Attach it through the GitHub web UI (issue/PR/release), then paste the
  returned `.../assets/....mp4` URL on its own line in the README so it renders
  as an inline player. See the comment in `README.md` for the exact steps.

## Guidance

- The sample work must be from [Aozora Bunko](https://www.aozora.gr.jp/)
  (public-domain Japanese literature) or other public-domain / synthetic text.
  Do not capture copyrighted manuscripts, real account identifiers, or API keys.
- Keep images reasonably small (aim for < 500 KB per PNG).
- Unlike the rest of `docs/`, this folder is published: it is part of the
  public README and is included when the public `main` branch is built.
