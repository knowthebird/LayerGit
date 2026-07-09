# VS Code Extension

The VS Code extension is a thin GUI over the LayerGit CLI. The CLI remains the
source of truth for behavior and safety checks.

## What It Shows

- layer stack
- composed tree
- visible, masked, hidden, stale, and untracked file states
- provenance through `layer explain`
- layer enable/disable and ordering actions
- write-layer selection

## File Workflows

The composed tree view exposes common CLI-backed workflows:

- apply file
- apply all changes
- use a layer for a file
- clear selection
- apply to another layer
- delete with explicit hide/source-delete/generated-copy choices
- create a new buildtree file and optionally apply it to a layer

The extension prompts before ambiguous actions. It shells out to commands such
as `layer use`, `layer apply`, `layer apply --to`,
`layer apply <path> --delete`, and `layer compose`.

## Run Locally

From the LayerGit repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

cd vscode-extension
npm ci
npm run compile
cd ..
```

Open the repo root in VS Code, then choose **Run > Start Debugging**. The
checked-in debug workspace at `.vscode/debug-target.code-workspace` lets the
Extension Development Host use this repo as its test workspace.

## Package Locally

```bash
cd vscode-extension
npm ci
npm run compile
npm run package
code --install-extension layergit-vscode-0.0.1.vsix
```

The packaged extension still shells out to the Python CLI, so install `layer`
first.

More extension notes live in [../vscode-extension/README.md](../vscode-extension/README.md).
