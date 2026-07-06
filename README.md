# LayerGit

[![CI](https://github.com/knowthebird/LayerGit/actions/workflows/ci.yml/badge.svg)](https://github.com/knowthebird/LayerGit/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

LayerGit composes multiple Git repositories into one generated workspace.

Each layer is still a normal Git repository. LayerGit clones or creates those
repos under `.layer/cache/`, then generates a `buildtree/` that tools like IDEs
and build systems can use as one source tree.

When two layers provide the same path, the higher layer wins by default. The
lower file is masked, not deleted. LayerGit records provenance so you can
explain which layer provided each visible file.

You can override the default choice for specific files, move layers to change
precedence, and apply `buildtree/` changes back to the appropriate layer repos.

License: Apache-2.0.

> Status: early alpha. LayerGit has unit tests and real-Git invariant tests, but
> it is still new. Use normal Git backups and review `layer status` and
> `layer diff` before applying changes to important repositories.

## Table of Contents

- [What is LayerGit?](#what-is-layergit)
- [Requirements](#requirements)
- [Install](#install)
- [Quick Start](#quick-start)
- [Try the Demo](#try-the-demo)
- [Core Concepts](#core-concepts)
- [Common Commands](#common-commands)
- [Advanced Features](#advanced-features)
- [VS Code Extension](#vs-code-extension)
- [Known Limitations](#known-limitations)
- [Alternatives and Related Projects](#alternatives-and-related-projects)
- [Development](#development)
- [Questions, Feedback, and Future POCs](#questions-feedback-and-future-pocs)
- [License](#license)

## What is LayerGit?

LayerGit is for projects where source files need to remain in separate Git
repositories, but an IDE, build system, vendor SDK, or workflow expects one
source tree.

LayerGit is not meant to replace existing Git, build-system, or multi-repo
tools. If one of those fits cleanly, use it. LayerGit is for cases where
separate Git repositories need to appear together as one generated source tree,
especially when layers may overlap and provenance matters.

Layer numbers count from bottom to top. `1` is the bottom/base layer, and the
highest number is the top/highest-precedence layer. `layer status` displays the
visual stack from top to bottom.

```text
Layer 2: component-c  top / higher precedence  provides common/util.c
Layer 1: component-b  bottom / lower precedence provides common/util.c
```

Because `component-c` is higher in the stack, `buildtree/common/util.c` comes
from `component-c`. The `component-b` version is masked and recorded in
provenance.

## Requirements

- Python `>=3.10`
- Git
- `pip` or `pipx`
- Node.js and npm only if you are developing or packaging the VS Code extension
- Node.js 20 or newer for VSIX packaging with the current extension packaging
  toolchain
- VS Code `^1.92.0` if you are running the extension locally

## Install

Install the CLI directly from GitHub:

```bash
python -m pip install git+https://github.com/knowthebird/LayerGit.git
layer --help
```

Or install it as an isolated command with `pipx`:

```bash
pipx install git+https://github.com/knowthebird/LayerGit.git
layer --help
```

For local development from a checkout, use a virtual environment:

```bash
git clone https://github.com/knowthebird/LayerGit.git
cd LayerGit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
layer --help
```

The installed package exposes both `layer` and `layergit`. `layer` is the
intended day-to-day command. The Python module form is useful during local
development or as a fallback:

```bash
layer status
layergit status
python3 -m layergit.cli status
```

## Quick Start

Use one directory as the LayerGit workspace, then add existing Git repositories
as layers. Source repositories can live anywhere; LayerGit clones them into
`.layer/cache/<layer-name>/` and generates `buildtree/`.

```bash
mkdir my-layergit-workspace
cd my-layergit-workspace

layer init --output ./buildtree

layer add ../repo-a product
layer add ../repo-b component-b
layer add ../repo-c component-c

layer status
layer compose
layer explain common/util.c
```

`layer init` creates `workspace-base` as a local Git-backed base layer by
default. Repo layers are added above it. `buildtree/` is generated output, and
higher layers win overlapping paths unless you choose a different provider with
`layer use`.

Use `layer init --no-base-layer` only when you want to start with no default
`workspace-base` local layer.

## Try the Demo

The overlap demo is local and network-free after the package is installed:

```bash
examples/overlap-demo.sh
```

It demonstrates temporary local Git repos, overlapping files, top-layer-wins
behavior, provenance, `layer use`, applying a `buildtree/` edit back to a layer,
and export.

## Core Concepts

- `layer.yaml` is the workspace recipe.
- `.layer/cache/<layer>/` contains normal Git repos.
- `buildtree/` is generated output for IDEs and build tools.
- Higher layers win path conflicts by default.
- Masked files are not deleted.
- `layer use` selects a different provider for one path.
- `layer apply` copies edits from `buildtree/` back to the owning layer repo.
- Git still handles add, commit, push, branch, and merge.

LayerGit writes supporting metadata:

- `layer.lock.yaml`: generated exact layer commits
- `.layer/ownership.json`: visible and masked file provenance
- `.layer/conflicts.json`: conflict and warning report

## Common Commands

| Command                       | Purpose                                              |
| ----------------------------- | ---------------------------------------------------- |
| `layer status`                | Show layer order, state, Git status, and write layer |
| `layer compose`               | Regenerate `buildtree/` from the current layers      |
| `layer explain <path>`        | Show which layer provides a file                     |
| `layer use <path> <layer>`    | Choose a specific layer for one path                 |
| `layer diff`                  | Show `buildtree/` changes that can be applied        |
| `layer apply <path>`          | Apply one edited file back to its owning layer       |
| `layer apply --new`           | Apply new unowned files to the write layer           |
| `layer -L <layer> git status` | Run Git inside a layer cache repo                    |

Common examples:

```bash
layer explain common/util.c
layer use common/util.c component-b
layer diff common/util.c
layer apply common/util.c
layer -L component-b git status
```

New unowned files in `buildtree/` are routed to the configured write layer:

```bash
layer diff --new
layer apply --new
layer -L workspace-base git status
```

## Advanced Features

### Local layers and write layer

Local layers are Git-backed repos created under `.layer/cache/`. They are useful
for experiments, local-only changes, or new files.

```bash
layer add --local local-edits
layer write local-edits
layer -L local-edits git status
```

### Moving layers

Move a layer in the stack with `layer move <layer> <position>`:

```bash
layer move component-b up
layer move component-b down
layer move component-b top
layer move component-b bottom
```

### Enabling and disabling layers

Temporarily hide or re-enable a layer without deleting its manifest entry or
cached repo:

```bash
layer disable component-c
layer status
layer enable component-c
```

### Clean compose

By default, `layer compose` updates LayerGit-owned files and preserves unknown
files in `buildtree/`, such as compiler outputs or IDE artifacts. Use explicit
clean mode only when you want to rebuild the output tree from scratch:

```bash
layer compose --clean
```

### Export

Export the current composed tree as a standalone directory or Git repo:

```bash
layer export ./merged-project --with-provenance
layer export ./merged-project --init-git
```

### JSON output

Several commands support JSON for tools and editor integrations:

```bash
layer status --json
layer tree --json
layer explain common/util.c --json
```

### Git passthrough

Use normal Git inside `.layer/cache/<layer>/` if needed, or run scoped Git
commands from the workspace root:

```bash
layer -L component-b git status
layer -L component-b git add common/util.c
layer -L component-b git commit -m "Fix shared utility"
```

Do not commit from `buildtree/`; it is generated output. Use `layer apply` first.

## VS Code Extension

The VS Code extension provides a thin GUI over the LayerGit CLI:

- Layers and status
- Composed Tree
- `layer explain` file provenance
- repo and local layer add/remove actions
- layer enable/disable actions
- write-layer selection through `layer write`
- file and folder provider selection through `layer use`

![LayerGit VS Code extension example](docs/vs-code-example.gif)

Run it locally:

```bash
# from the LayerGit repo root
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

Package a local `.vsix`:

```bash
cd vscode-extension
npm ci
npm run compile
npm run package
code --install-extension layergit-vscode-0.0.1.vsix
```

The packaged extension still shells out to the Python CLI, so install `layer`
first. More extension development notes live in
[vscode-extension/README.md](vscode-extension/README.md).

## Known Limitations

- Early alpha; test important workflows on copied repositories before relying on
  it.
- `buildtree/` is generated output. Use `layer apply` before committing changes.
- The VS Code extension is still local/development-oriented, not a Marketplace
  release.
- Conflict diagnostics are still evolving.

## Alternatives and Related Projects

LayerGit is not meant to replace existing Git, build-system, or multi-repo
tools. If one of these fits your workflow cleanly, use it.

LayerGit is aimed at a narrower case: separate Git repositories need to appear
together as one generated source tree, and overlapping paths need clear
precedence, masking, provenance, and apply-back behavior. See
[docs/ALTERNATIVES.md](docs/ALTERNATIVES.md) for a wider feature matrix.

| Tool / approach | What it does well | What LayerGit adds |
| --- | --- | --- |
| [multigit](https://github.com/capr/multigit) | Lightweight overlapped Git repositories in one working tree. Repositories act like layers, and the tool stays close to Git. | A separate generated `buildtree/`, visible/masked provenance, explicit per-file provider selection, apply-back from `buildtree/`, and VS Code integration. |
| [vcsh](https://github.com/RichiH/vcsh) | Maintains several Git repositories in one single directory, commonly for dotfiles/config sets. | Source-tree composition for IDE/build workflows, generated output, masking/provenance, and layer selection. |
| [Git submodules](https://git-scm.com/book/en/v2/Git-Tools-Submodules) | Keeps another Git repo as a subdirectory with separate history. | Allows multiple layers to contribute to the same logical tree and handles overlapping paths. |
| [git-subrepo](https://github.com/ingydotnet/git-subrepo) | Vendors an external repo into a subdirectory with pull/push support. | Keeps source repos separate and generates a composed output tree instead of merging into the parent repo. |
| [repo](https://gerrit.googlesource.com/git-repo/) | Manages many Git repos from manifests. | Adds layer precedence, masking, provenance, and generated source-tree composition. |
| [west](https://docs.zephyrproject.org/latest/develop/west/manifest.html) | Manages Zephyr-style multi-repo workspaces. | Is not tied to Zephyr and focuses on composing one generated tree from ordered layers. |
| [vcstool](https://github.com/dirk-thomas/vcstool) | Imports, exports, and operates across multiple VCS repositories. | Adds generated workspace composition and file-level provenance. |
| [myrepos / mr](https://myrepos.branchable.com/) | Runs commands across many repositories. | Generates one composed workspace for tools that expect a single source tree. |
| Monorepo | Puts everything in one repository. | Helps when repos must remain separate. |
| Build-system dependencies | Models dependencies in the build system. | Helps when the IDE/build/source layout is constrained and expects files to exist in one tree. |

## Development

Run Python tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest
```

Run coverage for the CLI and child LayerGit processes:

```bash
python -m pip install -e '.[dev]'
coverage erase
LAYERGIT_TEST_COVERAGE=1 coverage run --parallel-mode -m unittest
coverage combine
coverage report
coverage report --format=markdown > tests/coverage-summary.md
```

Compile the VS Code extension:

```bash
cd vscode-extension
npm ci
npm run compile
```

CI runs Python tests, coverage, the overlap demo, extension compile, and VSIX
packaging.

## Questions, Feedback, and Future POCs

LayerGit is an early alpha. Feedback is especially useful around:

- workflows where multiple repos need to appear as one source tree
- embedded, firmware, vendor SDK, legacy IDE, or generated workspace use cases
- cases where submodules, monorepos, or build-system dependencies do not fit
- examples that would make good future demos or proofs of concept

Open issues for bugs, workflow feedback, feature requests, or demo ideas.

## License

LayerGit is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
