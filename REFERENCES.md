# References

Last reviewed: 2026-10-19.

These are the research and technical sources used as background or references for NARRAVANT. Each entry explains its use and limits. A citation does not imply a complete reproduction of a paper's method or guarantee the quality of an individual audiobook.

## Story analysis

### REF-REAGAN

Andrew J. Reagan et al. **The emotional arcs of stories are dominated by six basic shapes.** 2016.

- Source: [arXiv:1606.07772](https://arxiv.org/abs/1606.07772).
- Use: conceptual background for emotional valence analysis and emotional arc shapes.
- Limits: individual works are not required to fit one of the six shapes. NARRAVANT's 1–7 valence scale, 0 for character absence, and 10-dimensional Shape+Slope DCT representation are application design choices, rather than the method evaluated in this paper.

### REF-SUSPENSE

David Wilmot and Frank Keller. **Modelling Suspense in Short Stories as Uncertainty Reduction over Neural Representation.** ACL 2020.

- Source: [ACL Anthology](https://aclanthology.org/2020.acl-main.161/), [DOI](https://doi.org/10.18653/v1/2020.acl-main.161).
- Use: conceptual background for analyzing tension through uncertainty, danger, conflict, time pressure, and release.
- Limits: NARRAVANT's −3 to +3 tension scale is an application convention, not a measurement or quality score established by this paper. Tension scores do not determine acting instructions mechanically.

### REF-TURNING-POINTS

Pinelopi Papalampidi, Frank Keller, and Mirella Lapata. **Movie Plot Analysis via Turning Point Identification.** EMNLP-IJCNLP 2019, pp. 1707–1717.

- Source: [ACL Anthology](https://aclanthology.org/D19-1180/), [DOI](https://doi.org/10.18653/v1/D19-1180).
- Use: the five turning-point labels—Opportunity, Change of Plans, Point of No Return, Major Setback, and Climax—and their association with scenes.
- Limits: this framework was studied for movie plots. It does not establish that every literary work contains all five points at fixed positions, and it does not justify inventing events to fit the framework.

## Source-grounded scene generation and local revision

### REF-R2

Zefeng Lin et al. **R²: A LLM Based Novel-to-Screenplay Generation Framework with Causal Plot Graphs.** 2025.

- Source: [arXiv:2503.15655](https://arxiv.org/abs/2503.15655).
- Use: background for extracting events, building causal graphs, planning scenes, and writing and checking against the original source.
- Limits: NARRAVANT preserves the source presentation order rather than reproducing breadth-first output ordering or iterative refinement. Local revisions share a limit of one revision per scene. The paper's evaluation results are not quality improvement measurements for this application.

### REF-DOC

Kevin Yang, Dan Klein, Nanyun Peng, Yuandong Tian. **DOC: Improving Long Story Coherence With Detailed Outline Control.** ACL 2023.

- Source: [ACL Anthology](https://aclanthology.org/2023.acl-long.190/).
- Use: background for detailed scene planning and generation and validation guided by scene goals and required information.
- Limits: NARRAVANT does not implement the paper's dedicated token-level controller. Preservation of the source plot and information disclosure order is evaluated separately for this application.

### REF-RE3

Kevin Yang, Yuandong Tian, Nanyun Peng, Dan Klein. **Re3: Generating Longer Stories With Recursive Reprompting and Revision.** EMNLP 2022.

- Source: [ACL Anthology](https://aclanthology.org/2022.emnlp-main.296/).
- Use: background for repeatedly supplying plans, character states, and relevant earlier scenes during writing.
- Limits: NARRAVANT does not reproduce the full candidate generation and reranking process. Creating an original story and adapting an existing work have different goals.

### REF-SELF-REFINE

Aman Madaan et al. **Self-Refine: Iterative Refinement with Self-Feedback.** 2023.

- Source: [arXiv:2303.17651](https://arxiv.org/abs/2303.17651).
- Use: background for local revisions that pass the first draft and specific feedback back into writing.
- Limits: NARRAVANT combines source evidence, preservation constraints, and automated checks without an unlimited self-evaluation loop. Scores from the same model are not treated as human evaluation.

## Script and speech specifications

### REF-FOUNTAIN

Fountain. **Syntax.**

- Source: [official syntax specification](https://fountain.io/syntax/).
- Use: title pages, scene headings, scene numbers (`#N#`), forced character cues (`@`), dialogue, and parentheticals in script generation, parsing, editing, and playback.
- Limits: NARRAVANT's heading prefixes, sequential integer scene numbers, explicit speaker cues, and narrator aliases (`@ナレーター` / `@Narrator`) are application conventions. Interpreting parentheticals as speech style is NARRAVANT's playback behavior, rather than a TTS feature of Fountain.

### REF-GEMINI-TEXT

Google. **Gemini 4 Argon**, **What's new in Gemini 4 Argon**, and **Google Gen AI Python SDK**. Checked 2026-10-21.

- Sources: [Model specifications](https://ai.google.dev/gemini-api/docs/models/gemini-4-argon), [Generate Content migration guide](https://ai.google.dev/gemini-api/docs/generate-content/latest-model), [Python SDK](https://github.com/googleapis/python-genai).
- Use: text analysis and adaptation with Gemini 4 Argon or Gemini 3.8 Flash. `GEMINI_MODEL` selects the model; `backend/config.yaml` defines the model-specific output budget and HTTP/stream timeouts. Argon's thinking tokens share its output budget; the migration guide recommends at least 32,768 output tokens and a 20-minute HTTP timeout. The application requires Python SDK 2.30.1 or newer.
- Limits: configuration and synthetic compatibility tests do not establish model availability for an account, real-generation quality, latency, or billed cost. Argon does not generate speech; TTS uses a separate model. Recheck model limits and SDK support when updating the integration.

### REF-GEMINI-TTS

Google. **Text-to-speech generation (TTS)** and **Generating content: SpeechMetadata.**

- Sources: [GenerateContent TTS guide](https://ai.google.dev/gemini-api/docs/generate-content/speech-generation), [SpeechMetadata API reference](https://ai.google.dev/api/generate-content#SpeechMetadata).
- Use: separate spoken text from sustained acting directions through `parts[].speechMetadata.style` and stream playback from the first PCM samples. Local pause and vocal delivery tags remain in the text. Requests are split by sentence and reuse the assigned voice ID. The application inserts configured silence at speaker changes and after spoken Scene Headings, without adding silence between other sentences by the same speaker.
- Limits: acceptance of an API field does not demonstrate acting quality, timing, or clarity. Tags do not guarantee millisecond durations or eliminate API waiting time. Overlapping custom-voice playback is rejected before a request is sent. New adaptations use narrator explanations or natural sequential turns; this is not a playback fallback for existing pipe notation. SDK, model, and API support must be rechecked when updating the integration. The application does not retain generated audio.

### REF-GEMINI-VOICE-DESIGN

Google. **Voice design.**

- Sources: [Voice Design guide](https://ai.google.dev/gemini-api/docs/voice-design), [Speech generation guide](https://ai.google.dev/gemini-api/docs/generate-content/speech-generation).
- Use: custom voices with persistent traits such as perceived age, timbre, and accent. Stateful custom voices are retained for one year after their last use; synthesizing speech with a voice or using it as a remix base resets the retention period. Situational acting instructions are handled separately through TTS style.
- Limits: specifying a trait does not establish that the generated voice expresses it successfully. Perceived age and other voice qualities require listening evaluation. Retention policy and API behavior can change; check Google's current documentation when updating the integration.

## Numerical methods

### REF-SCIPY

SciPy. **scipy.fft.dct** and **scipy.interpolate.PchipInterpolator.**

- Sources: [DCT reference](https://docs.scipy.org/doc/scipy/reference/generated/scipy.fft.dct.html), [PCHIP reference](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html).
- Use: discrete cosine transforms and PCHIP interpolation for NARRAVANT's emotional arc representation.
- Limits: the 64-point sampling, six level components, three slope components, one flatness component, weights, and normalization belong to NARRAVANT's vector design. They are not a required algorithm from the emotional arc research above.
