# Contributing

Before opening a pull request:

- run the Python tests
- run the real-Git invariant tests
- compile the VS Code extension if touched
- update docs for user-visible behavior changes
- add or update tests for safety-sensitive behavior
- keep `layer apply`, delete, staging, dry-run, mount-path, and JSON behavior
  covered when those areas change

Useful local checks:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest
coverage erase
LAYERGIT_TEST_COVERAGE=1 coverage run --parallel-mode -m unittest
coverage combine
coverage report --fail-under=100

cd vscode-extension
npm ci
npm run compile
```
