# Contributing to NARRAVANT

[日本語](CONTRIBUTING.ja.md)

Bug reports and suggestions for improvement are welcome. Please discuss code changes in an issue before opening a pull request. Acceptance and response times depend on the proposal and the maintainer's availability.

Please treat others with respect. Reports and discussions in Japanese are welcome too.

## Reports and suggestions

Check the [existing issues](https://github.com/irioka/narravant/issues), then use the appropriate issue template.

For bugs, include the app version, OS and browser, steps to reproduce, expected behavior, and actual results. Write "unknown" for anything you cannot identify. A short, synthetic example you wrote yourself is helpful for reproducing the problem without sharing a real manuscript.

- Do not publish API keys, `.env` files, private manuscripts, personal information, or actual voice IDs.
- Remove sensitive information from logs and screenshots before attaching them.
- For vulnerabilities, follow the [security policy](SECURITY.md) instead of publishing details in an issue or pull request.
- For barriers to using the app, see the [accessibility statement](ACCESSIBILITY.md) and the accessibility issue template.

## Pull requests

1. Fork the public repository and create a working branch.
2. Keep changes focused and avoid unrelated modifications.
3. Include tests or verification results appropriate to any behavior changes.
4. Describe the reason for the change, its effect on users, checks performed, and anything not verified. Include screenshots for UI changes when practical.

Submit code and materials that you are entitled to provide under this repository's [MIT license](LICENSE). Identify the source and license of any third-party materials or dependencies you add.

## Development environment and checks

See the [README](README.md) for setup and startup instructions. Use the Python version specified in `backend/pyproject.toml` and the Node.js version in `.nvmrc`, together with uv and npm.

From the repository root, run:

```bash
cd backend
uv sync --locked
uv run pytest -q
uv run ruff check src tests scripts
cd ../frontend
npm ci
npm run test:run
npm run lint
npm run build
```

The regular automated tests use synthetic data and fakes. They do not require a Gemini API key or paid API calls. Real Gemini generation, analysis, and TTS incur charges; keep these separate from the regular tests. If you run them, understand the cost and data being sent, and use your own key and data you are permitted to share.
