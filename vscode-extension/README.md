# LayerGit VS Code Extension

This extension is a thin VS Code UI over the LayerGit CLI. It shells out to the
installed `layer` command or to the repo-local Python venv during development.

## Local Development

Install the Python CLI from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
layer --help
```

Install and compile extension dependencies:

```bash
cd vscode-extension
npm ci
npm run compile
```

Open the repository root in VS Code and choose **Run > Start Debugging**. The
debug configuration opens `.vscode/debug-target.code-workspace`, which points
the Extension Development Host at the repo root without reusing the normal VS
Code window for that folder.

After changing TypeScript, rerun `npm run compile` or leave `npm run watch`
running, then reload the Extension Development Host window. Changes to
`package.json` require stopping and starting the debug session.

## Package a VSIX

The current extension packaging toolchain requires Node.js 20 or newer.

```bash
cd vscode-extension
npm ci
npm run compile
npm run package
code --install-extension layergit-vscode-0.0.1.vsix
```

The packaged extension still requires the Python CLI to be installed separately.
