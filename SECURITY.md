# Security policy

[日本語](SECURITY.ja.md)

## Supported versions and intended use

The latest official release is the primary target for investigation and fixes. Backporting fixes to older releases is considered individually.

NARRAVANT is a local application intended to run on your own computer. Its security has not been validated for deployment as a network-accessible service for multiple users. Handle manuscripts and instructions sent to the Gemini API, API keys, and locally stored works and settings with care.

## Reporting a vulnerability

Do not post vulnerability details, exploit instructions, or sensitive information in public issues or pull requests.

Use GitHub's **Security → Advisories → Report a vulnerability** to contact the maintainer privately. You can open the [private vulnerability reporting form](https://github.com/irioka/narravant/security/advisories/new) directly.

If you cannot access the form, open a regular issue stating only that the private reporting form is unavailable to you. Do not include vulnerability details.

Include the following information where available:

- Affected versions and features
- Potential impact
- Minimal reproduction steps using synthetic data
- Relevant environment details, such as the OS and browser

Do not send actual API keys, private manuscripts, or personal information, even in a private report. Use redacted values or synthetic data instead. Investigate only in environments you are authorized to use, without affecting other people's data or external services.

## Handling reports

The maintainer reviews the report and its impact, then discusses any additional information, mitigations, fixes, and disclosure with the reporter. Fix and disclosure schedules depend on the impact and progress of the investigation; there is no fixed response or resolution deadline. Use [Issues](https://github.com/irioka/narravant/issues) for ordinary bugs and feature requests.
