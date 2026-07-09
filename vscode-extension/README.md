# LayerGit VS Code Extension

This extension is a thin VS Code UI over the LayerGit CLI. It shells out to the
installed `layer` command or to the repo-local Python venv during development.

## Using the View

Open the LayerGit activity bar view. If the current VS Code folder does not have
a `layer.yaml`, the Layers view shows an **Initialize LayerGit Workspace** item.
Click it to run `layer init`; the views refresh after initialization and show
the created layers.

When a workspace exists, the Layers view shows visible action rows for common
workflows:

- **Add Layer...** opens a picker for **Add Repo Layer** or **Add Local Layer**.
- **Remove Layer...** opens a layer picker and then confirms removal.
- **Apply All Changes** runs `layer apply --all`.
- **Refresh Layers** reloads CLI JSON state.
- **Open LayerGit Output** opens the extension output channel.

The action rows are separated from the layer stack and composed files by dashed
section rows so command items do not blend into generated file entries.

Layer rows are shown top-to-bottom. Drag a layer onto another layer to move it
above that target in the stack; the extension calls `layer move` and then
refreshes instead of reordering locally. Right-click a layer for write-layer,
enable/disable, move, remove, apply-layer, cache-repo, and Git-status actions.

The Composed Tree view shows clear empty states. Without a LayerGit workspace it
points back to initialization in the Layers view. If no generated tree exists,
it shows **Compose / Refresh Tree**, which runs `layer compose`. Stale generated
state is shown with a warning item.

Paths hidden by `layer use` assignment still appear in the Composed Tree with a
hidden icon and no open-file action. Right-click the path to explain it, choose
another provider, or clear the selection.

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
