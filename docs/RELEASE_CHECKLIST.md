# Release Checklist

LayerGit is early alpha. Treat each release as a shareable snapshot, not a
stability guarantee.

## Before tagging

- Review `README.md` install, demo, CLI, and VS Code extension instructions.
- Run the Python test suite:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 python -m unittest
  ```

- Run coverage and update the checked-in summary if behavior changed:

  ```bash
  coverage erase
  LAYERGIT_TEST_COVERAGE=1 coverage run --parallel-mode -m unittest
  coverage combine
  coverage report --fail-under=100
  coverage report --format=markdown > tests/coverage-summary.md
  ```

- Run the demo:

  ```bash
  examples/overlap-demo.sh
  ```

- Verify local package installation in a clean virtual environment:

  ```bash
  python3 -m venv /tmp/layergit-release-venv
  /tmp/layergit-release-venv/bin/python -m pip install .
  /tmp/layergit-release-venv/bin/layer --help
  /tmp/layergit-release-venv/bin/layergit --help
  ```

- Compile and package the VS Code extension with Node.js 20 or newer:

  ```bash
  cd vscode-extension
  npm ci
  npm run compile
  npm run package
  ```

## Version and tag

- Update `version` in `pyproject.toml`.
- Update `version` in `vscode-extension/package.json` when shipping a matching
  VSIX.
- Confirm `vscode-extension/package-lock.json` is in sync.
- Commit the release changes.
- Tag the release:

  ```bash
  git tag v0.1.0
  git push origin main --tags
  ```

## After tagging

- Create a GitHub release with the main changes, known limitations, and install
  commands.
- Attach the generated `.vsix` if sharing the extension outside the Marketplace.
- Smoke test GitHub install:

  ```bash
  python -m pip install git+https://github.com/knowthebird/LayerGit.git
  ```

- Smoke test `pipx` install:

  ```bash
  pipx install git+https://github.com/knowthebird/LayerGit.git
  ```
