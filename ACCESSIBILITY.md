# Accessibility

[日本語](ACCESSIBILITY.ja.md)

## Approach

NARRAVANT aims to make importing manuscripts, editing scripts, assigning voices, and starting or stopping playback easier for people who use different ways to interact with the app. Reports of controls that are difficult or impossible to use help guide improvements.

## Current verification status

A dedicated accessibility audit, hands-on screen reader testing, and keyboard-only testing across all screens have not been performed. WCAG conformance has not been verified or declared.

The following are areas to check and improve. They are not claims of completed support; individual improvements will be checked within a defined scope.

- Reach and operate the main controls, including playback and stop, using a keyboard
- Keep focus visible and move it appropriately when dialogs open or close
- Make input and button names, errors, and playback status understandable through screen readers and similar tools
- Keep essential content and controls available when the display is enlarged
- Communicate chart and status information without relying on color alone

The scope will be reviewed as verification progresses. Support for every environment or a completion date for improvements is not promised.

## Reporting a barrier

Use the [accessibility issue template](https://github.com/irioka/narravant/issues/new?template=03_accessibility.yml). If the template is difficult to complete, open a [regular issue](https://github.com/irioka/narravant/issues/new) and share whatever information you can.

- The screen or feature you were trying to use
- What you expected and what made the task difficult
- If known, the app version, OS, browser, and assistive technology used
- Any alternative method you can currently use, if known

Here, "assistive technology" means software or devices that help people operate the app or understand information, such as screen readers that announce screen content and controls, screen magnifiers, and voice control.

Do not attach API keys, private manuscripts, or personal information. The maintainer will review reports and prioritize consideration of barriers that prevent people from completing the main tasks.
