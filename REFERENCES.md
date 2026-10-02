# References

Last reviewed: 2026-10-02.

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

## Script and speech specifications

### REF-FOUNTAIN

Fountain. **Syntax.**

- Source: [official syntax specification](https://fountain.io/syntax/).
- Use: title pages, scene headings, scene numbers (`#N#`), forced character cues (`@`), dialogue, and parentheticals in script generation, parsing, editing, and playback.
- Limits: NARRAVANT's heading prefixes, sequential integer scene numbers, explicit speaker cues, and narrator aliases (`@ナレーター` / `@Narrator`) are application conventions. Interpreting parentheticals as speech style is NARRAVANT's playback behavior, rather than a TTS feature of Fountain.

### REF-GEMINI-TTS

Google. **Text-to-speech generation (TTS)** and **Generating content: SpeechMetadata.**

- Sources: [TTS guide](https://ai.google.dev/gemini-api/docs/speech-generation), [SpeechMetadata API reference](https://ai.google.dev/api/generate-content#SpeechMetadata).
- Use: speech generation with spoken text and per-utterance acting instructions sent separately through `parts[].speechMetadata.style`, followed by streaming PCM playback.
- Limits: acceptance of an API field does not demonstrate audible acting quality, timing, or intelligibility. Supported models, SDKs, and features must be checked against current documentation when updating the integration. NARRAVANT does not retain generated audio.

### REF-GEMINI-VOICE-DESIGN

Google. **Voice design.**

- Source: [Voice Design guide](https://ai.google.dev/gemini-api/docs/voice-design).
- Use: custom voices with persistent traits such as perceived age, timbre, and accent. Situational acting instructions are handled separately through TTS style.
- Limits: specifying a trait does not establish that the generated voice expresses it successfully. Perceived age and other voice qualities require listening evaluation.

## Numerical methods

### REF-SCIPY

SciPy. **scipy.fft.dct** and **scipy.interpolate.PchipInterpolator.**

- Sources: [DCT reference](https://docs.scipy.org/doc/scipy/reference/generated/scipy.fft.dct.html), [PCHIP reference](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html).
- Use: discrete cosine transforms and PCHIP interpolation for NARRAVANT's emotional arc representation.
- Limits: the 64-point sampling, six level components, three slope components, one flatness component, weights, and normalization belong to NARRAVANT's vector design. They are not a required algorithm from the emotional arc research above.
