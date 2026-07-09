# LayerGit

[![CI](https://github.com/knowthebird/LayerGit/actions/workflows/ci.yml/badge.svg)](https://github.com/knowthebird/LayerGit/actions/workflows/ci.yml)
[![CodeQL](https://github.com/knowthebird/LayerGit/actions/workflows/codeql.yml/badge.svg)](https://github.com/knowthebird/LayerGit/actions/workflows/codeql.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

LayerGit composes multiple normal Git repositories into one generated,
explainable source workspace.

Use it when your source needs to stay split across repos, but your IDE, build
system, vendor SDK, or legacy workflow expects one source tree.

Each layer stays a normal Git repository under `.layer/cache/<layer>/`.
LayerGit generates `buildtree/` from those layers, records which layer supplied
each visible file, and shows which lower-layer files were masked or
intentionally hidden.

> **Status:** Experimental. LayerGit is tested with real Git repositories, but
> it is still a new tool. Try it on demo or disposable repos first, and review
> `layer doctor`, `layer diff`, and dry-run output before applying changes to
> important repositories.

## 30 Second Demo

From a LayerGit checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

examples/overlap-demo.sh
```

The current demo creates temporary local Git repos and shows:

- ordered layers
- same-path masking
- `layer explain`
- `layer use`
- applying a `buildtree/` edit back to a layer

<!-- TODO: add a richer vendor-board-app demo and short terminal or VS Code GIF. -->

## What Problem Does LayerGit Solve?

LayerGit is for projects where code must remain split across repositories, but
tools around the project expect one source tree.

Examples:

- vendor SDK + board support + application code
- product or customer variants
- local experimental patch layers
- legacy IDE or build workflows that expect files in fixed paths
- situations where copying files manually makes provenance unclear

If a monorepo, submodule, package manager, or build-system dependency model
works cleanly, use that.

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

For local development from a checkout:

```bash
git clone https://github.com/knowthebird/LayerGit.git
cd LayerGit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
layer --help
```

The installed package exposes both `layer` and `layergit`. The Python module
form is useful during local development or as a fallback:

```bash
layer status
layergit status
python3 -m layergit.cli status
```

## Quick Start

Use one directory as the LayerGit workspace, then add existing Git repositories
as layers:

```bash
mkdir my-layergit-workspace
cd my-layergit-workspace

layer init --output ./buildtree
layer add ../vendor-sdk vendor-sdk
layer add ../board-support board-support
layer add ../app app --mount /app

layer status
layer overlaps
layer explain drivers/gpio.c
layer doctor
```

Higher layers win overlapping paths by default. Lower-layer copies are masked,
not deleted. Use `layer use` to select a different existing layer for a file,
`layer use --hide` to intentionally suppress an inherited file, and
`layer unuse` to return to normal precedence.

## Core Ideas

- `layer.yaml` describes the workspace.
- `.layer/cache/<layer>/` contains isolated normal Git repos.
- `buildtree/` is generated output for IDEs and build tools.
- Layers can mount at `/` or under subfolders such as `/app`.
- Higher layers win same-path conflicts by default.
- Lower-layer copies are masked, not deleted.
- `layer explain` shows visible, masked, or hidden provenance.
- `layer apply`, `layer apply --to`, and `layer apply <path> --delete` are
  explicit write operations.

See [docs/CONCEPTS.md](docs/CONCEPTS.md) for the deeper mental model.

## Safety Model

Layer repos under `.layer/cache/<layer>/` are the source of truth.
`buildtree/` is generated output. LayerGit changes source repos only through
explicit `apply`, `apply --to`, or `apply <path> --delete` commands, and it
never commits or pushes for you.

Run `layer doctor` before risky work. Use dry-run previews when you want to see
what would happen first:

```bash
layer compose --dry-run
layer apply drivers/gpio.c --dry-run
layer apply drivers/gpio.c --to board-support --dry-run
layer apply drivers/gpio.c --delete --dry-run
layer use drivers/gpio.c vendor-sdk --dry-run
layer use legacy/unused.c board-support --hide --dry-run
```

| Command | Source repo changed? | Selected layer changed? | Stages by default? | Purpose |
|---|---:|---:|---:|---|
| `layer use <path> <layer>` | no | yes | no | Select an existing layer for a file |
| `layer use <path> <layer> --hide` | no | yes | no | Suppress inherited lower-layer file |
| `layer apply <path>` | yes | no | no | Apply edit to current owning layer |
| `layer apply <path> --to <layer>` | yes | yes | new files only | Apply current buildtree content to a chosen layer |
| `layer apply <path> --stage` | yes | no | yes | Apply and stage edit |
| `layer apply <path> --delete` | yes | yes/cleared | yes | Delete from owning source layer |
| `layer compose` | no | no | no | Regenerate `buildtree` |

See [docs/SAFETY.md](docs/SAFETY.md) for dirty buildtree protection, staging
rules, stale ownership, unowned files, and delete behavior.

## VS Code Extension

LayerGit includes a local VS Code extension that stays a thin GUI over the CLI.
It shows the layer stack, composed tree, provenance, file layer selection,
apply-to-layer/delete prompts, and layer ordering actions.

![LayerGit VS Code extension example](docs/vs-code-example.gif)

See [docs/VS_CODE_EXTENSION.md](docs/VS_CODE_EXTENSION.md) and
[vscode-extension/README.md](vscode-extension/README.md) for setup and
development notes.

## Alternatives and Related Projects

LayerGit is not meant to replace Git, submodules, build systems, package
managers, or multi-repo fetch tools.

Use the existing tool if it cleanly fits your workflow.

LayerGit is aimed at a narrower case: separate Git repositories need to appear
together as one generated source tree, and overlapping paths need clear
precedence, masking, provenance, and apply-back behavior.

| Tool / approach | Good fit | Where LayerGit differs |
|---|---|---|
| `multigit` | Lightweight direct overlay of Git repos into one shared working tree | LayerGit uses isolated source repos plus a generated `buildtree`, with visible/masked/hidden provenance and controlled apply-back |
| `vcsh` | Dotfiles/config repos in one working tree | LayerGit targets source-workspace composition, path precedence, masking, and IDE/build workflows |
| `git-subrepo` | Vendor another repo into a subdirectory with pull/push support | LayerGit does not merge vendor content into the parent repo; it composes a generated workspace from separate repos |
| `repo` / `west` / `vcstool` | Fetching/managing multi-repo workspaces | LayerGit focuses on composing overlapping or mounted repos into one generated tree |
| Monorepo | Everything can live in one repository | LayerGit helps when repos must remain separate |
| Build-system dependencies | Build layout can be modeled directly | LayerGit helps when tooling expects files to already exist in one tree |

See [docs/ALTERNATIVES.md](docs/ALTERNATIVES.md) for a fuller comparison.

## Trust and Verification

LayerGit is tested with real Git repositories, not only mocked file operations.

The test suite includes safety invariants for source-repo isolation,
masked-layer preservation, dry-run behavior, dirty buildtree protection,
mount-path mapping, apply/delete staging behavior, and CLI JSON compatibility.

LayerGit never commits or pushes during normal layer, compose, apply, delete,
or selection workflows. Source repos are changed only by explicit apply/delete
operations or by direct user-run Git commands inside a layer repo.

See [docs/INVARIANTS.md](docs/INVARIANTS.md) and
[docs/SAFETY.md](docs/SAFETY.md).

## Detailed Documentation

- [Concepts](docs/CONCEPTS.md)
- [Safety](docs/SAFETY.md)
- [Safety invariants](docs/INVARIANTS.md)
- [Alternatives](docs/ALTERNATIVES.md)
- [Demos](docs/DEMOS.md)
- [VS Code extension](docs/VS_CODE_EXTENSION.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)

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

## Status / Feedback

LayerGit is an early alpha. Feedback is especially useful around embedded,
firmware, vendor SDK, legacy IDE, or generated-workspace workflows where
multiple repos need to appear as one source tree.

Open issues for bugs, workflow feedback, feature requests, or demo ideas.

## License

LayerGit is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
