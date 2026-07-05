# Layered Git Workspace Prototype Guide

Working name: **LayerGit** / **Layer Workspace**

This document is intended as a design/reference guide for building a prototype CLI tool that composes multiple Git repositories into one clean, deterministic, buildable workspace.

The central idea is not merely "multiple Git repos in one folder." The useful product is a **manifest-driven layered source workspace**:

```text
source repos + layer rules = composed build tree
```

The composed tree can then be used by an IDE, build system, legacy project, embedded build, Eclipse/CDT workspace, or exported as a normal single repository.

---

## 1. Problem statement

Some projects are split across multiple Git repositories, but the actual build system expects a single source tree.

Example:

```text
repo A = product/build environment
repo B = component or binary B
repo C = component or binary C
repo D = shared common code
```

But B and C may each include their own copies of files from D:

```text
repo-b/common/util.c
repo-c/common/util.c
repo-d/common/util.c
```

In some legacy or embedded build environments, especially Eclipse/CDT-style source builds, duplicate file names or duplicate source paths can cause build conflicts. The build wants one flattened, valid tree.

Today, teams often handle this by copying files manually, using awkward submodules, maintaining duplicate common code, forcing everything into a monorepo, or building custom scripts.

The goal of this tool is to make that composition explicit, reproducible, explainable, and easy to use.

---

## 2. Product goal

Build a CLI tool that allows a developer to define ordered Git-backed layers and produce a composed output tree.

The tool should answer:

- Which repos make up this workspace?
- Which layer has priority when files overlap?
- Which files are visible in the final tree?
- Which files are masked by higher layers?
- Which files are masked by default top-layer precedence?
- Which source repo owns a visible file?
- Can the final composed tree be exported as a normal repo?

The CLI should be simple enough that users do not feel like they are learning a new build system.

---

## 3. Core metaphor

Layers work like a simplified CAD, Photoshop, or PowerPoint merge/flatten model:

```text
Layer 1 = bottom/base
Layer 2 = above Layer 1
Layer 3 = above Layer 2
Top layer = highest precedence
```

Higher layers override, mask, or replace same-path files from lower layers by default. This should be automatic but not hidden: the tool must record and explain every masked file.

---

## 4. Non-goals for prototype

The prototype should avoid trying to solve everything.

Do **not** initially build:

- A full Git replacement
- A complete GUI
- A complex/full VS Code extension beyond the thin MVP sidebar described later
- A complex package manager
- A remote service
- A custom filesystem driver
- Deep merge of source files
- Automatic semantic code conflict resolution
- Support for every possible Git command as a first-class command

The prototype should be a small CLI with a clear manifest and deterministic output.

---

## 5. Recommended architecture

Use a materialized output tree rather than a live union filesystem.

Suggested workspace layout:

```text
my-workspace/
  layer.yaml                  # user-authored manifest
  layer.lock.yaml             # generated lockfile with exact commits
  .layer/
    cache/
      product/
      component-b/
      component-c/
      common/
    ownership.json            # generated file ownership/provenance map
    conflicts.json            # generated conflict report
  buildtree/
    ...                       # composed output tree used by IDE/build system
```

The tool should clone source repos into `.layer/cache/`, then compose visible files into `buildtree/`.

This is safer than having multiple Git repositories directly mutate the same working tree.

---

## 6. Minimal command philosophy

People dislike learning large new tools. Keep the first command surface small.

Recommended everyday commands:

```bash
layer init
layer add
layer remove
layer disable
layer enable
layer status
layer pull
layer -L <layer> git
layer explain
layer merge
layer export
```

File-level precedence commands should also exist because they map to familiar visual layer operations, but they should be documented as simple aliases rather than a large advanced command family:

```bash
layer raise <layer> <file>
layer lower <layer> <file>
layer use <file> <layer>
layer unuse <file>
```

Principle:

```text
Common layer workflows get direct commands.
Uncommon Git workflows go through `layer -L <layer> git`.
```

This avoids recreating all of Git.

---

## 7. CLI invocation and install target

During early Python prototyping, it is acceptable for commands to be run as:

```bash
python3 -m layergit.cli init
python3 -m layergit.cli add ./fixtures/repo-a product
python3 -m layergit.cli status
```

However, that should be treated as a development/testing invocation, not the intended user experience. The user-facing command should be short and memorable:

```bash
layer init
layer add ./fixtures/repo-a product
layer status
```

The prototype should support both forms if possible:

- `python3 -m layergit.cli ...` for local development before installation.
- `layer ...` after editable install or package install.

Recommended packaging approach:

```toml
[project.scripts]
layer = "layergit.cli:main"
```

Then developers can install the working tree in editable mode:

```bash
python3 -m pip install -e .
layer status
```

If the name `layer` conflicts with an existing command on a target system, the package can also expose a longer alias:

```toml
[project.scripts]
layer = "layergit.cli:main"
layergit = "layergit.cli:main"
```

Documentation should use `layer ...` as the primary command and mention `python3 -m layergit.cli ...` only as a development fallback. This keeps the learning curve low and avoids making users feel like they are operating a Python module instead of a normal CLI tool.

---

## 8. Layer selectors

Commands should accept both names and indexes.

Examples:

```bash
layer pull all
layer pull top
layer pull 1
layer pull 2..4
layer pull 1,3,4
layer pull common
layer -L component-b git status
```

Avoid requiring a `#` character in commands because shells and documentation can make that awkward.

Recommended selector behavior:

| Selector | Meaning |
|---|---|
| `all` | All layers |
| `top` | Highest-priority layer |
| `1` | Layer by index |
| `2..4` | Inclusive layer range |
| `1,3,4` | Explicit layer list |
| `common` | Layer by name |

By default, commands that operate on composition should use enabled layers only. Commands that manage layer records, such as `layer status`, `layer enable`, `layer disable`, `layer remove`, and `layer -L <layer> git`, should still be able to reference disabled layers by name or index.

Suggested selector behavior for disabled layers:

| Selector | Recommended behavior |
|---|---|
| `all` | All enabled layers for compose/pull/export-style operations; all layers for administrative commands |
| `all-layers` | Explicitly include enabled and disabled layers |
| `enabled` | Enabled layers only |
| `disabled` | Disabled layers only |

---

## 9. Standard Git command behavior

The tool should not replace Git. It should scope Git operations to the correct layer.

Simple rule:

```text
Use normal Git inside an individual layer repo.
Use layer commands at the composed workspace level.
```

### Workspace root

The workspace root may itself be a normal Git repository, but only for tracking workspace-level files such as:

```text
layer.yaml
layer.lock.yaml
README.md
project documentation
prototype scripts
```

Standard Git commands from the workspace root should not accidentally commit the generated build tree or all source files from every layer.

Recommended `.gitignore` for the workspace root:

```gitignore
.layer/
buildtree/
```

This allows the LayerGit workspace configuration to be versioned without treating generated output as source.

### Generated build tree

The generated build tree should not be a Git repository by default. It is a materialized view produced from the configured layers.

Avoid putting a `.git/` directory inside `buildtree/` unless the user explicitly runs an export command such as:

```bash
layer export ./merged-project --init-git
```

Reason: standard Git cannot know which layer owns a file, which lower-layer files were masked, or which repository should receive a commit.

Instead of this:

```bash
cd buildtree
git status
git commit -m "Fix utility"
```

Users should do this:

```bash
layer status
layer explain common/util.c
layer commit common -m "Fix utility"
```

or use the passthrough:

```bash
layer -L common git status
layer -L common git commit -m "Fix utility"
```

### Cached layer repositories

Each layer is backed by a normal Git repository in `.layer/cache/<layer-name>/`. Standard Git commands may be run directly inside an individual cached layer repo:

```bash
cd .layer/cache/common
git status
git pull
git checkout feature/foo
git commit -m "Fix common utility"
```

However, if users bypass the layer CLI, the composed tree and lockfile may become stale. After direct Git usage inside a cached layer, users should run:

```bash
layer status
layer pull --no-fetch
```

For the prototype, `layer pull --no-fetch` can mean “re-read layer repos and recompose without fetching from remotes.” If that flag is not implemented, provide an equivalent recompose command later.

The tool should eventually detect out-of-band changes and warn:

```text
Layer common changed outside the layer CLI. Recompose the workspace before building.
```

### Preferred Git passthrough

The preferred way to run normal Git commands is through:

```bash
layer -L <selector> git <git-command> [git-args...]
```

Examples:

```bash
layer -L common git status
layer -L common git log --oneline
layer -L common git checkout feature/foo
layer -L common git commit -m "Fix shared code"
layer -L all git fetch --prune
layer -L 2..4 git status
```

This preserves the full power of Git while keeping the target layer explicit.

Safety rule:

```text
Normal Git commands are safe when run against a specific layer.
They are unsafe or misleading when run against the composed build tree.
```

---


## 10. Gitignore and repository hygiene

There are two different repository contexts to protect:

1. The **LayerGit tool repository**, where this CLI is being developed.
2. An **end-user LayerGit workspace**, where layers are composed into a build tree.

Both should make it hard to accidentally commit generated workspace data, local test repositories, personal files, or large cloned dependencies.

### Tool repository `.gitignore`

The LayerGit tool repo should ignore prototype workspaces, generated build trees, temporary Git fixtures, exported repos, Python artifacts, and editor caches.

Recommended tool-repo `.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# Virtual environments
.venv/
venv/
env/

# Packaging/build output
build/
dist/
*.egg-info/

# Local environment
.env
.env.*
!.env.example

# LayerGit generated workspaces and outputs
.layer/
buildtree/
layer.lock.yaml
.layer-exports/
merged-project/
merged-*/

# Local scratch/test areas that may contain real repos or personal data
scratch/
workspaces/
local-workspaces/
manual-test-workspaces/
.tmp-layergit/

# Temporary Git fixture repos created by tests or Codex
fixtures/tmp/
tests/tmp/
tests/fixtures/tmp/
*.bundle
*.patch

# Editor/OS noise
.vscode/settings.json
.idea/
.DS_Store
Thumbs.db
```

Avoid committing real cloned test repositories into the tool repo. For repeatable tests, prefer creating temporary Git repositories during the test run in a temp directory. If static fixtures are needed, store ordinary source files under `tests/fixtures/source/` and have the tests initialize Git repos from those files.

### End-user workspace `.gitignore`

When a user runs `layer init`, the tool should create or update the workspace root `.gitignore` by default. This makes first use safer and easier.

Recommended generated block:

```gitignore
# BEGIN LayerGit
# Generated cache of layer repositories
/.layer/

# Generated composed output tree
/buildtree/

# Optional local exports/scratch outputs
/.layer-exports/
/merged-project/
/merged-*/
# END LayerGit
```

If the user selected a custom output path, the generated block should use that path instead of `/buildtree/`:

```gitignore
# BEGIN LayerGit
/.layer/
/my-custom-buildtree/
/.layer-exports/
# END LayerGit
```

Do not overwrite an existing `.gitignore`. Append or update only the block between `# BEGIN LayerGit` and `# END LayerGit`.

Useful `layer init` options:

```bash
layer init --output ./buildtree
layer init --no-gitignore
layer init --update-gitignore
```

Recommended default:

```text
layer init updates .gitignore unless --no-gitignore is provided.
```

### What should be tracked in a workspace repo

A workspace repo should normally track:

```text
layer.yaml
layer.lock.yaml
README.md
documentation
project-level scripts
```

It should normally ignore:

```text
.layer/
buildtree/
exported merged repos
local scratch workspaces
```

The lockfile should be tracked by default because it records exact layer commits and makes the composed workspace reproducible.

### Compose only tracked files by default

For safety, composition should use Git-tracked files from each layer by default, not every file sitting in the layer working directory.

Recommended source enumeration:

```bash
git -C .layer/cache/<layer-name> ls-files
```

This prevents untracked personal notes, local build outputs, temporary files, editor files, and test artifacts from being copied into `buildtree/` or exported.

A later advanced option may allow untracked files intentionally:

```bash
layer compose --include-untracked
layer export ./merged-project --include-untracked
```

But the default should be:

```text
Only Git-tracked files from each layer participate in composition.
```

### Do not edit layer repo `.gitignore` files by default

Each layer repo's `.gitignore` belongs to that layer's upstream project. The tool should not modify it automatically.

If LayerGit needs local-only ignore behavior inside a cached layer repo, it may write to that repo's local Git exclude file instead:

```text
.layer/cache/<layer-name>/.git/info/exclude
```

This avoids changing upstream source files.

### Export `.gitignore` behavior

`layer export` should export the visible composed tree exactly as composed. If a visible `.gitignore` file exists in the composed tree, export it like any other file.

If `--with-provenance` is used, provenance files may be added explicitly:

```text
.layer-provenance.json
.layer-lock.yaml
```

If `--init-git` is used, initialize Git only in the export target, not in `buildtree/`.

### Safety checks

The CLI should warn when a user appears to be doing something risky:

```text
WARNING: output path is not ignored by .gitignore: ./buildtree
WARNING: .layer/ cache is not ignored by .gitignore
WARNING: composing from untracked files is disabled by default
WARNING: selected export path is inside the tool repository
```

These should be warnings, not blockers, unless the action would overwrite existing user data.

---
## 11. Command details

### `layer init`

Create a new layer workspace.

Example:

```bash
layer init
layer init --output ./buildtree
```

Should create:

```text
layer.yaml
layer.lock.yaml              # generated after first sync/compose
.gitignore                   # created or updated unless --no-gitignore is provided
.layer/
buildtree/
```

`layer init` should add a LayerGit-managed `.gitignore` block that ignores `.layer/` and the configured output tree. It should not overwrite unrelated user ignore rules.

Initial `layer.yaml` example:

```yaml
workspace:
  output: ./buildtree

layers: []

composition:
  same_path_policy: top_wins

conflicts:
  duplicate_basename_policy: warn
```

---

### `layer add`

Add a new layer. This should be used for the first layer and later layers.

Examples with explicit names:

```bash
layer add git@example.com/repo-a.git product
layer add git@example.com/repo-b.git component-b
layer add git@example.com/repo-c.git component-c
layer add git@example.com/repo-d.git common --top
```

Examples using inferred names:

```bash
layer add git@example.com/component-b.git
layer add git@example.com/common.git --top
layer add ../local-common-repo
```

If the optional name argument is not provided, the tool should infer a default layer name from the repository name. This keeps the first-use experience simple.

Recommended inference rules:

1. For a remote URL, use the final repository path segment.
2. Remove a trailing `.git`.
3. For a local path, use the final directory name.
4. Normalize into a safe layer name: lowercase by default, replace spaces/unsafe punctuation with `-`, collapse repeated `-`, and trim leading/trailing `-`.
5. If the inferred name already exists, append a numeric suffix: `name-2`, `name-3`, etc.

Examples:

| Input repo | Inferred layer name |
|---|---|
| `git@example.com/team/common.git` | `common` |
| `https://example.com/repos/component-b.git` | `component-b` |
| `../Vendor SDK` | `vendor-sdk` |
| second clone of `common.git` | `common-2` |
| third clone of `common.git` | `common-3` |

The generated name should be shown to the user:

```text
Added layer 3: common-2
Source: git@example.com/team/common.git
```

Users can still override the inferred name:

```bash
layer add git@example.com/team/common.git common-vendor
```

Positioning examples:

```bash
layer add git@example.com/repo-d.git common --after 3
layer add git@example.com/repo-d.git common --before component-c
layer add ../local-common-repo common --top
layer add git@example.com/repo-d.git --after 3
layer add git@example.com/repo-d.git --before component-c
layer add ../local-common-repo --top
```

Default behavior should probably be:

1. Infer a layer name if the optional name argument was not provided.
2. Add layer to manifest with `enabled: true`.
3. Clone or register source repo into `.layer/cache/<layer-name>/`.
4. Recompose `buildtree/`.

Useful options:

```bash
layer add <repo>
layer add <repo> <name>
layer add <repo> --before <selector>
layer add <repo> --after <selector>
layer add <repo> --top
layer add <repo> --revision <branch-or-commit>
layer add <repo> --no-sync
layer add <repo> --no-compose
```

Name collision safety:

- If the user provides the optional name argument and that name already exists, fail with a clear error unless a future `--rename-if-needed` option is added.
- If the name is inferred and already exists, automatically append the next numeric suffix.
- Numeric suffixes should use the normalized base name, not the full URL.

---

### `layer remove`

Remove a layer from the workspace manifest.

Examples:

```bash
layer remove 3
layer remove component-c
layer remove component-c --keep-cache
layer remove component-c --delete-cache
```

Default should be safe: remove from manifest and recompose, but do not delete cached repo contents unless explicitly requested.

---

### `layer disable` and `layer enable`

Disable or re-enable a layer without deleting it from the workspace.

Examples:

```bash
layer disable component-c
layer disable 3
layer enable component-c
layer enable 3
```

Default behavior:

- New layers are enabled by default.
- A disabled layer remains in `layer.yaml`.
- A disabled layer's cached repository remains in `.layer/cache/<layer-name>/`.
- A disabled layer is excluded from `buildtree/` composition.
- A disabled layer is excluded from export and merge unless explicitly included by a future option.
- A disabled layer should not mask lower layers and should not win file precedence.
- Existing file-specific precedence rules involving a disabled layer should be retained but ignored while the layer is disabled.

This gives users a way to temporarily hide a layer without losing configuration, revision, cache, or precedence settings.

Suggested manifest representation:

```yaml
layers:
  - name: product
    repo: git@example.com/repo-a.git
    revision: main
    enabled: true

  - name: component-c
    repo: git@example.com/repo-c.git
    revision: main
    enabled: false
```

Suggested status display:

```text
Layers:
  1 product        enabled   clean      main @ abc123
  2 component-b    enabled   modified   main @ def456
  3 component-c    disabled  clean      main @ 9912aa
  4 common         enabled   clean      main @ 77beef  top
```

Useful aliases may be added later for discoverability:

```bash
layer hide component-c     # alias for disable
layer show component-c     # alias for enable
```

The prototype should implement `disable` and `enable` first. `hide` and `show` can remain optional aliases.

---

### `layer status`

Show a high-level view of the workspace.

Example output:

```text
Layers:
  1 product        enabled   clean      main @ abc123
  2 component-b    enabled   modified   main @ def456
  3 component-c    enabled   clean      main @ 9912aa
  4 common         enabled   clean      main @ 77beef  top

Composed tree:
  output: ./buildtree
  visible files: 1248
  masked files: 37
  conflicts: 2

Conflicts:
  src/driver.c exists in component-b and component-c
  include/config.h exists in product and component-c

Modified files:
  buildtree/common/util.c -> common
```

Support machine-readable output:

```bash
layer status --json
```

This will be useful for a future VS Code extension.

---

### `layer pull`

Pull one or more layers.

Recommended default:

```bash
layer pull
```

Meaning: pull all layers and recompose.

Other examples:

```bash
layer pull all
layer pull top
layer pull common
layer pull 2..4
layer pull 1,3,4
layer pull --no-compose
```

By default, `layer pull` should operate on enabled layers. If a disabled layer is explicitly selected, the command should be allowed because the user intentionally targeted it:

```bash
layer pull component-c
layer pull disabled
```

After pulling enabled layers, recompose the output tree. Pulling only disabled layers should update their cache/lock information but should not change `buildtree/` except for metadata such as status.

---

### `layer -L <layer> git`

Generic passthrough for normal Git commands.

Examples:

```bash
layer -L common git status
layer -L common git log --oneline
layer -L component-b git checkout feature/my-branch
layer -L all git fetch --prune
layer -L 2..4 git status
```

This avoids creating dozens of wrapper commands.

Potential behavior:

```bash
layer -L <selector> git <git-command> [git-args...]
```

If selector expands to multiple layers, run command once per layer and prefix output with the layer name. `layer -L <layer> git` should allow disabled layers to be selected because it targets the underlying repo, not the composed output.

---

### `layer explain`

Explain where a file came from and what it masks.

Example:

```bash
layer explain common/util.c
```

Example output:

```text
common/util.c

Visible file:
  layer: common
  repo: git@example.com/repo-d.git
  commit: 77beef
  source path: common/util.c

Masked lower-layer files:
  component-b/common/util.c
  component-c/common/util.c

Reason:
  default top-layer-wins precedence
```

Support JSON:

```bash
layer explain common/util.c --json
```

This is one of the most important commands because explainability is a core product value.

If a file is only present in a disabled layer, `layer explain <file>` should say that no enabled layer currently provides the file and list disabled providers separately:

```text
optional/feature.c

Visible file:
  none

Disabled providers:
  component-c/optional/feature.c

Reason:
  component-c is disabled
```

---

### File selection commands: `use`, `unuse`

By default, when the same output path exists in multiple layers, the topmost layer wins and lower-layer versions are masked. Users should be able to select which layer provides an individual file without moving an entire layer.

The public commands are:

```bash
layer use <file> <layer>
layer unuse <file>
```

Examples:

```bash
layer use common/util.c common
layer use common/util.c component-b
layer unuse common/util.c
```

Meaning:

| Command | Behavior |
|---|---|
| `use` | Select a layer as the provider for the file path |
| `unuse` | Remove the explicit file selection and return to normal precedence |

These commands should affect only the named file path, not the layer's global position.

For example, if the global layer order is:

```text
1 product
2 component-b
3 component-c
4 common
```

and all three of `component-b`, `component-c`, and `common` contain `common/util.c`, the default visible file is from `common`.

This command:

```bash
layer use common/util.c component-b
```

should make `component-b` the visible owner for `common/util.c` only. Other files should continue to follow normal global layer order.

The tool should persist this as a file-level precedence rule in `layer.yaml` rather than silently editing repositories. Suggested manifest representation:

```yaml
file_precedence:
  common/util.c:
    order:
      - component-c
      - common
      - component-b
```

The order is bottom-to-top for that file path only, so `component-b` wins for `common/util.c`.

After any file precedence change, the tool should recompose `buildtree/` and update `.layer/ownership.json`.

`layer explain common/util.c` should show that the result came from a file-specific precedence rule:

```text
common/util.c

Visible file:
  layer: component-b

Masked files:
  common/common/util.c
  component-c/common/util.c

Reason:
  file-specific precedence rule in layer.yaml
```

Safety behavior:

- If the named file exists in only one layer, report that no precedence change is needed.
- If the named layer does not provide that file, fail with a clear error.
- If the named layer is disabled, either fail with a clear message or allow the rule to be recorded but explain that it will not affect composition until the layer is enabled. The safer MVP behavior is to fail unless `--allow-disabled` is provided later.
- If the file path is currently generated, ignored, or excluded, explain why it cannot be moved.
- Support aliases with hyphens later if desired: `send-to-top` and `send-to-bottom`.

---

### `layer merge`

Create a new layer by merging/flattening selected existing layers.

This is analogous to merging layers in CAD, Photoshop, or PowerPoint.

Safe MVP behavior:

```bash
layer merge 2..4 --name combined-components
```

Result:

```text
Before:
  1 product
  2 component-b
  3 component-c
  4 common

After:
  1 product
  2 combined-components
```

The new layer should contain the visible composed result of the selected layers.

Potential options:

```bash
layer merge 2..4 --name combined-components
layer merge component-b,component-c,common --name combined-components
layer merge 2..4 --name combined-components --init-git
layer merge 2..4 --name combined-components --with-provenance
```

Dangerous later feature, not MVP:

```bash
layer merge 2..4 --into common
```

This would modify an existing layer and should require explicit confirmation or `--force`.

---

### `layer export`

Export the composed final tree to a normal directory or Git repository.

Examples:

```bash
layer export ./merged-project
layer export ./merged-project --init-git
layer export ./merged-project --with-provenance
```

Purpose:

- Create an escape hatch from the layered workflow.
- Allow users to hand off the composed result.
- Allow creating a normal monorepo snapshot.
- Support release/audit archives.

Export is different from merge:

```text
merge  = creates a new layer inside the layered workspace
export = creates a standalone source tree or Git repo outside the workspace
```

---

## 12. Manifest format

Initial simple manifest:

```yaml
workspace:
  output: ./buildtree

composition:
  same_path_policy: top_wins

layers:
  - name: product
    repo: git@example.com/repo-a.git
    revision: main
    enabled: true

  - name: component-b
    repo: git@example.com/repo-b.git
    revision: main
    enabled: true

  - name: component-c
    repo: git@example.com/repo-c.git
    revision: main
    enabled: true

  - name: common
    repo: git@example.com/repo-d.git
    revision: main
    enabled: true

conflicts:
  duplicate_basename_policy: warn
```

Default same-path behavior should be easy to understand:

```text
If two or more layers provide the same output path, the topmost layer wins.
Lower-layer versions are masked and recorded in ownership/provenance output.
```

This means exact-path overlap is not a conflict by default. It is a normal layer behavior. The tool should still make it visible in `layer status` and `layer explain` so users are not surprised.

Layer entries should support `enabled`. If omitted, `enabled` defaults to `true` for backward compatibility and ease of hand-written manifests:

```yaml
layers:
  - name: component-c
    repo: git@example.com/repo-c.git
    revision: main
    enabled: false
```

Disabled layers remain configured but are ignored during composition, export, and default merge operations.

File-specific precedence rules can override global layer order for a single path:

```yaml
file_precedence:
  common/util.c:
    order:
      - component-b
      - component-c
      - common
```

The order is bottom-to-top for that file path only. In the example above, `common` wins for `common/util.c` even if a later command changes the global layer order.

The file precedence commands update this section:

```bash
layer use common/util.c common
layer lower common common/util.c
layer raise component-b common/util.c
layer unuse common/util.c
```

Potential later additions:

```yaml
workspace:
  output: ./buildtree
  compose_mode: copy   # copy | symlink | hardlink

composition:
  same_path_policy: top_wins   # top_wins | error

layers:
  - name: component-b
    repo: git@example.com/repo-b.git
    revision: main
    include:
      - src/**
      - common/**
    exclude:
      - docs/**
    map:
      - from: source/**
        to: src/**

  - name: common
    repo: git@example.com/repo-d.git
    revision: main

file_precedence:
  common/util.c:
    order:
      - component-b
      - component-c
      - common

conflicts:
  duplicate_basename_policy: warn   # ignore | warn | error
  forbid_duplicate_basenames:
    - "**/*.c"
    - "**/*.cpp"
    - "**/*.h"
```

Prototype should start simple and avoid path mapping unless needed.

---

## 13. Lockfile format

Generated `layer.lock.yaml` should record exact commits.

Example:

```yaml
layers:
  - name: product
    repo: git@example.com/repo-a.git
    revision: main
    commit: abc123
    enabled: true

  - name: component-b
    repo: git@example.com/repo-b.git
    revision: main
    commit: def456
    enabled: true

  - name: component-c
    repo: git@example.com/repo-c.git
    revision: main
    commit: 9912aa
    enabled: false

  - name: common
    repo: git@example.com/repo-d.git
    revision: main
    commit: 77beef
    enabled: true
```

The lockfile matters for reproducibility, CI, releases, and debugging. It may record disabled layers too, because a disabled layer is still part of the workspace definition and may be re-enabled later.

---

## 14. Ownership/provenance map

The tool should generate an ownership database so files can be explained and eventually committed back to the right repo.

Example `.layer/ownership.json`:

```json
{
  "common/util.c": {
    "visible": {
      "layer": "common",
      "repo": "git@example.com/repo-d.git",
      "commit": "77beef",
      "source_path": "common/util.c"
    },
    "masked": [
      {
        "layer": "component-b",
        "source_path": "common/util.c",
        "commit": "def456"
      },
      {
        "layer": "component-c",
        "source_path": "common/util.c",
        "commit": "9912aa"
      }
    ],
    "reason": "default top-layer-wins precedence"
  }
}
```

---

## 15. Composition behavior

Prototype composition algorithm:

1. Read `layer.yaml`.
2. Treat layers with omitted `enabled` fields as `enabled: true`.
3. Ensure all enabled layers are cloned into `.layer/cache/<layer-name>/`. Disabled layers may remain cached but are not required for composition.
4. Enumerate Git-tracked files in each enabled layer by default using `git ls-files`.
5. Walk tracked files from bottom enabled layer to top enabled layer.
6. For each file, compute its output path.
7. If output path is unused, copy it into `buildtree/`.
8. If output path already exists, the higher enabled layer wins by default. The previous visible file becomes masked.
9. If a file-specific precedence rule exists for that output path, apply it only across enabled layers that provide the file.
10. Write `.layer/ownership.json`, including visible and masked files.
11. Write `.layer/conflicts.json` for policy violations such as forbidden duplicate basenames.
12. Write/update `layer.lock.yaml`.

Disabled-layer policy:

```text
Disabled layers are hidden from the composed tree.
They do not provide visible files.
They do not mask lower layers.
They are retained in the manifest and can be re-enabled later.
```

Default same-path policy:

```text
Top layer wins.
Lower-layer same-path files are masked, not treated as errors.
```

This supports the visual-layer mental model and makes the first-use experience easier. Adding a layer should produce a usable composed tree whenever possible.

The tool should still be transparent. `layer status` should report masked file counts, and `layer explain <file>` should show every layer that contributed or was hidden.

Strict same-path behavior can be supported as an optional policy later:

```yaml
composition:
  same_path_policy: error
```

But `top_wins` should be the default.

---

## 16. Conflict handling

Exact same-path overlap is not a conflict by default. It is normal layer behavior:

```text
common/util.c exists in component-b, component-c, and common.
Visible file: common/common/util.c because common is the topmost layer.
Masked files: component-b/common/util.c, component-c/common/util.c.
```

Conflicts should be reserved for cases where the composed tree violates a configured policy or cannot be safely produced.

Examples:

### Forbidden duplicate basename

Some legacy/Eclipse-style builds may fail when two source files have the same basename, even if they are in different folders. This should be a configurable policy.

```yaml
conflicts:
  duplicate_basename_policy: error
  forbid_duplicate_basenames:
    - "**/*.c"
    - "**/*.cpp"
```

Example error:

```text
ERROR: duplicate source basename forbidden by policy

driver.c appears as:
  layer 2 component-b: src/driver.c
  layer 3 component-c: platform/driver.c
```

### Missing or disabled source for file precedence rule

```text
ERROR: file precedence rule references a layer that does not provide the file

common/util.c:
  requested visible layer: component-b
  component-b does not contain common/util.c
```

If a precedence rule references a disabled layer, the rule should be retained but ignored while the layer is disabled:

```text
WARNING: file precedence rule references disabled layer component-b
common/util.c will use the highest enabled layer instead
```

### Excluded or generated file movement

```text
ERROR: cannot move generated file between layers

build/version.h is generated during composition and has no source layer.
```

The tool should suggest possible fixes:

```text
Options:
  1. Use layer use <file> <layer> to choose a visible owner
  2. Use layer raise/lower to adjust file-level precedence
  3. Exclude one source file from a layer
  4. Rename/remap one file
  5. Change duplicate_basename_policy to warn if this build system allows it
```

---

## 17. Export behavior

`layer export ./merged-project` should copy the composed visible tree into a standalone directory. Disabled layers should not contribute files to export unless a future explicit option is added.

`layer export ./merged-project --init-git` should also initialize a normal Git repository and commit the result.

Possible generated commit message:

```text
Export composed layered workspace
```

`--with-provenance` should add something like:

```text
.layer-provenance.json
.layer-lock.yaml
```

inside the exported project.

This is important because it gives users a safe escape hatch:

```text
Layered workspace in, ordinary Git repo out.
```

---

## 18. VS Code extension MVP

The VS Code extension should be a **thin UI over the LayerGit CLI**, not a second implementation of the layer engine.

The CLI remains the source of truth. The extension should call CLI commands, consume JSON output, and display the result.

### 18.1 Target user flow

The MVP extension should support this flow:

1. User installs the extension.
2. User clicks a LayerGit icon in the VS Code Activity Bar.
3. A LayerGit view opens in the Primary Side Bar, in the same general area as Explorer.
4. If the current VS Code workspace contains a LayerGit workspace, the extension identifies it.
5. If `buildtree/` or the configured output tree exists, the extension shows it.
6. If no LayerGit workspace exists, the extension offers an **Initialize Workspace** action.
7. The side bar contains two main views:
   - **Layers**: layer stack, order, enabled/disabled state, Git status, branch, dirty/clean state.
   - **Composed Tree**: explorer-style view of the composed build tree, with layer/provenance information for selected files.
8. User can select a file in the composed tree and see which layer provides the visible file and which lower layers are masked.
9. User can use context actions to move a file up/down in layer precedence.

### 18.2 VS Code terminology

Use these VS Code concepts:

```text
Activity Bar        = far-left icon strip
View Container      = custom side bar container opened by the LayerGit icon
Primary Side Bar    = area where Explorer/Search/Source Control normally appear
Tree View           = hierarchical list inside the side bar
Command Palette     = Ctrl/Cmd+Shift+P commands
```

The MVP should contribute:

```text
Activity Bar icon: LayerGit
View Container: LayerGit
Tree View 1: Layers
Tree View 2: Composed Tree
```

### 18.3 Side bar layout

Recommended visual layout:

```text
LayerGit

LAYERS
  4 common          enabled   main   clean   top
  3 component-c     disabled  main   clean
  2 component-b     enabled   dev    dirty
  1 product         enabled   main   clean   bottom

COMPOSED TREE
  src/
    main.cc                 component-b
    common/
      util.cc               common
      driver.cc             common
  include/
    config.h                product
```

Show the top layer at the top of the Layers view because that matches visual layer tools such as CAD, Photoshop, and PowerPoint.

### 18.4 Workspace detection

When the extension activates, it should inspect the current VS Code workspace folder.

Detection order:

1. If `layer.yaml` exists in the workspace root, treat that folder as the LayerGit workspace.
2. If the active file is inside `buildtree/`, walk upward to find `layer.yaml`.
3. If no `layer.yaml` exists, show a welcome/empty state.

Empty state example:

```text
No LayerGit workspace found.

[Initialize Workspace]
[Open Existing layer.yaml]
```

The extension should not initialize automatically. Initialization should require a user action.

### 18.5 Layers view

The top view should show layer-level information.

Each layer node should show:

- Layer index
- Layer name
- Enabled/disabled state
- Branch name if available
- Dirty/clean Git status
- Whether it is top or bottom
- Conflict or warning count if relevant

Example:

```text
4 common        enabled   main   clean   top
3 component-c   disabled  main   clean
2 component-b   enabled   dev    dirty
1 product       enabled   main   clean   bottom
```

Layer context menu actions:

```text
Enable Layer
Disable Layer
Pull Layer
Open Layer Repo
Move Layer Up
Move Layer Down
Send Layer to Top
Send Layer to Bottom
Remove Layer
Explain Layer
```

For MVP, only implement actions that already exist in the CLI. It is acceptable for unavailable actions to be omitted until the CLI supports them.

### 18.6 Composed Tree view

The bottom view should show the generated build tree, not the raw layer cache.

Reason:

```text
The user cares what the build system sees.
```

Each file node should show its visible owner if practical:

```text
main.cc          component-b
util.cc          common
config.h         product
```

File selection should reveal provenance details. This can be shown as child nodes under the selected file, a detail node, a hover, or an output/details panel.

Example file detail:

```text
common/util.cc
  visible from: common
  source path: common/util.cc
  masked from:
    component-b/common/util.cc
    component-c/common/util.cc
  reason: top-layer precedence
```

File context menu actions:

```text
Explain File
Raise File
Lower File
Send File to Top
Send File to Bottom
Open Visible Buildtree File
Open Source Layer File
Show Masked Versions
```

The MVP should prioritize:

1. Explain File
2. Raise File
3. Lower File
4. Send File to Top
5. Send File to Bottom

Opening masked versions can come later if it complicates the prototype.

### 18.7 Required CLI JSON support

The extension depends on stable JSON output from the CLI.

Required CLI commands for the VS Code MVP:

```bash
layer status --json
layer list --json
layer tree --json
layer explain <file> --json
layer enable <layer>
layer disable <layer>
layer raise <layer> <file>
layer lower <layer> <file>
layer use <file> <layer>
layer unuse <file>
```

Useful command runner actions:

```bash
layer init
layer pull
layer pull <layer>
layer -L <layer> git status
```

The extension should call the installed `layer` command by default. During development, it may support a configurable fallback command such as:

```bash
python3 -m layergit.cli
```

Recommended extension setting:

```json
{
  "layergit.command": "layer"
}
```

For local prototype testing, a developer can set:

```json
{
  "layergit.command": "python3 -m layergit.cli"
}
```

### 18.8 Suggested JSON shapes

`layer status --json` should return enough information for the Layers view.

Example:

```json
{
  "workspace": "/path/to/workspace",
  "output": "buildtree",
  "layers": [
    {
      "index": 1,
      "name": "product",
      "enabled": true,
      "position": "bottom",
      "branch": "main",
      "revision": "abc1234",
      "dirty": false,
      "status": "clean"
    },
    {
      "index": 2,
      "name": "component-b",
      "enabled": true,
      "branch": "dev",
      "revision": "def5678",
      "dirty": true,
      "status": "dirty"
    }
  ],
  "conflicts": [],
  "warnings": []
}
```

`layer tree --json` should return enough information for the Composed Tree view.

Example:

```json
{
  "workspace": "/path/to/workspace",
  "output": "buildtree",
  "files": [
    {
      "path": "src/main.cc",
      "type": "file",
      "visibleLayer": "component-b",
      "visibleLayerIndex": 2,
      "maskedByThisFile": []
    },
    {
      "path": "common/util.cc",
      "type": "file",
      "visibleLayer": "common",
      "visibleLayerIndex": 4,
      "maskedByThisFile": [
        "component-b",
        "component-c"
      ]
    }
  ]
}
```

`layer explain <file> --json` should return provenance for one file.

Example:

```json
{
  "path": "common/util.cc",
  "visible": {
    "layer": "common",
    "layerIndex": 4,
    "sourcePath": "common/util.cc",
    "revision": "77beef1"
  },
  "masked": [
    {
      "layer": "component-b",
      "layerIndex": 2,
      "sourcePath": "common/util.cc",
      "revision": "def5678"
    },
    {
      "layer": "component-c",
      "layerIndex": 3,
      "sourcePath": "common/util.cc",
      "revision": "9912aaa"
    }
  ],
  "reason": "top-layer precedence"
}
```

### 18.9 Extension project structure

Suggested structure if the extension lives in the same repository:

```text
vscode-extension/
  package.json
  tsconfig.json
  src/
    extension.ts
    cli.ts
    workspace.ts
    layersView.ts
    composedTreeView.ts
    commands.ts
    models.ts
  media/
    layergit-icon.svg
```

Suggested responsibilities:

```text
extension.ts          activate/deactivate extension
cli.ts                run LayerGit CLI and parse JSON
workspace.ts          detect layer.yaml/buildtree
layersView.ts         TreeDataProvider for Layers view
composedTreeView.ts   TreeDataProvider for Composed Tree view
commands.ts           register VS Code commands/context menu actions
models.ts             TypeScript interfaces for CLI JSON
```

The extension should not import Python modules or read `.layer/ownership.json` directly unless that becomes a documented stable API. Prefer CLI JSON output.

### 18.10 Minimal `package.json` contribution model

The extension should contribute:

- An Activity Bar container named `LayerGit`
- A `layergit.layers` Tree View
- A `layergit.composedTree` Tree View
- Commands for refresh, initialize, explain current file, enable/disable layer, and file precedence actions
- Context menu actions on layer and file tree items

Conceptual contribution shape:

```json
{
  "contributes": {
    "viewsContainers": {
      "activitybar": [
        {
          "id": "layergit",
          "title": "LayerGit",
          "icon": "media/layergit-icon.svg"
        }
      ]
    },
    "views": {
      "layergit": [
        {
          "id": "layergit.layers",
          "name": "Layers"
        },
        {
          "id": "layergit.composedTree",
          "name": "Composed Tree"
        }
      ]
    },
    "commands": [
      {
        "command": "layergit.refresh",
        "title": "LayerGit: Refresh"
      },
      {
        "command": "layergit.init",
        "title": "LayerGit: Initialize Workspace"
      },
      {
        "command": "layergit.explainFile",
        "title": "LayerGit: Explain File"
      }
    ]
  }
}
```

### 18.11 MVP extension commands

Recommended VS Code commands:

```text
LayerGit: Refresh
LayerGit: Initialize Workspace
LayerGit: Pull All Layers
LayerGit: Explain Current File
LayerGit: Enable Layer
LayerGit: Disable Layer
LayerGit: Raise File
LayerGit: Lower File
LayerGit: Send File to Top
LayerGit: Send File to Bottom
LayerGit: Open Manifest
```

These commands should call the CLI and refresh the two Tree Views after success.

### 18.12 Error handling in extension

If the CLI is missing:

```text
LayerGit CLI not found.
Install the package or set layergit.command to the correct command.
```

If no workspace is found:

```text
No LayerGit workspace found in this VS Code folder.
```

If JSON parsing fails:

```text
LayerGit returned invalid JSON for command: layer status --json
```

If a file move command fails, show the CLI's stderr message without hiding it.

### 18.13 MVP extension acceptance tests

A useful extension prototype should pass these scenarios:

1. Install/open extension in a VS Code workspace with `layer.yaml`.
2. Click the LayerGit Activity Bar icon.
3. Layers view displays layer names, order, enabled/disabled state, branch, and dirty/clean status.
4. Composed Tree view displays files from `buildtree/`.
5. Selecting or right-clicking a file can run `layer explain <file> --json`.
6. File detail shows visible layer and masked layers.
7. Right-clicking a file can call `layer use <file> <layer>` where supported by the CLI.
8. Right-clicking a layer can enable or disable it.
9. After an action completes, both views refresh.
10. If no `layer.yaml` exists, the extension shows an Initialize Workspace action.
11. The extension works with a configurable CLI command path via `layergit.command`.

### 18.14 Defer until after MVP

Do not implement these in the first VS Code extension unless the basics are already stable:

- Custom webview layer graph
- Drag-and-drop layer reordering
- Deep integration with VS Code Source Control provider API
- Inline editor decorations for every file
- Multi-root workspace support beyond a simple first folder
- Automatic initialization without user action
- Reimplementation of layer composition in TypeScript

---

## 19. Prototype implementation suggestion

Python is a good prototype language because:

- Easy Git subprocess orchestration
- Easy YAML/JSON handling
- Easy filesystem operations
- Easy CLI creation
- Fast iteration with Codex

Suggested Python libraries:

- `argparse` or `typer` for CLI
- `subprocess` for Git calls
- `pathlib` for paths
- `shutil` for copying/export
- `json` for generated maps
- `yaml` via PyYAML for manifest/lockfile


Recommended `pyproject.toml` entry point:

```toml
[project]
name = "layergit"
version = "0.0.1"

[project.scripts]
layer = "layergit.cli:main"
layergit = "layergit.cli:main"
```

For local prototype usage before installation, the same CLI should also work as:

```bash
python3 -m layergit.cli status
```

Potential package structure:

```text
layergit/
  __init__.py
  cli.py
  manifest.py
  gitops.py
  selectors.py
  composer.py
  status.py
  export.py
  merge.py
  explain.py
  models.py

tests/
  test_selectors.py
  test_manifest.py
  test_composer.py
  test_export.py
```


If the VS Code extension is included in the same repository, keep it separate from the Python package:

```text
vscode-extension/
  package.json
  tsconfig.json
  src/
    extension.ts
    cli.ts
    workspace.ts
    layersView.ts
    composedTreeView.ts
    commands.ts
    models.ts
  media/
    layergit-icon.svg
```

Do not duplicate Python layer logic in the extension. The extension should call the CLI and parse JSON.

---

## 20. Minimum viable prototype acceptance tests

A useful prototype should pass these scenarios.

### Test 1: Create workspace

```bash
layer init --output ./buildtree
```

Expected:

- `layer.yaml` exists.
- `.layer/` exists.
- `buildtree/` exists or is created on compose.

### Test 2: Add first layer

```bash
layer add ./fixtures/repo-a product
layer status
```

Expected:

- Layer appears in manifest.
- Repo is registered/cloned into cache.
- Files appear in `buildtree/`.

### Test 3: Infer layer names when the optional name argument is omitted

```bash
layer add ./fixtures/repo-a
layer add ./fixtures/repo-a
layer status
```

Expected:

- First layer name is inferred from the repo/path, such as `repo-a`.
- Second layer gets a numeric suffix, such as `repo-a-2`.
- Cache paths use the final layer names.
- The generated names are printed to the user.
- If the user explicitly provides an already-used the optional name argument, the command fails instead of silently renaming it.

### Test 4: Add overlapping layers with default top-wins behavior

```bash
layer add ./fixtures/repo-b component-b
layer add ./fixtures/repo-c component-c
```

If both provide `common/util.c`, expected:

- The topmost layer's `common/util.c` is visible.
- The lower-layer version is masked, not treated as an error.
- `layer status` reports masked files.
- `layer explain common/util.c` shows visible and masked files.

### Test 5: File precedence movement

With `common/util.c` provided by `component-b`, `component-c`, and `common`, run:

```bash
layer use common/util.c component-b
```

Expected:

- `component-b` becomes the visible owner of `common/util.c`.
- Other files still follow normal global layer order.
- `layer.yaml` records a file-specific precedence rule.
- `layer explain common/util.c` says the visible result comes from file-specific precedence.

### Test 6: Disable and re-enable a layer

```bash
layer disable component-c
layer status
layer explain common/util.c
layer enable component-c
layer status
```

Expected:

- `component-c` remains in `layer.yaml` with `enabled: false` after disable.
- The cached repo is not deleted.
- Disabled layer files are removed from or excluded from `buildtree/`.
- Disabled layer files do not mask lower enabled layers.
- `layer status` clearly marks the layer as disabled.
- `layer enable component-c` restores the layer to composition and recomposes `buildtree/`.

### Test 7: Export

```bash
layer export ./merged-project --init-git
```

Expected:

- `merged-project/` exists.
- It contains only visible composed files.
- It has a `.git/` directory.
- Initial commit exists.

### Test 8: JSON output

```bash
layer status --json
layer explain common/util.c --json
```

Expected:

- Valid JSON suitable for a future VS Code extension.


### Test 9: VS Code extension MVP data support

The CLI should provide enough stable JSON for the extension MVP:

```bash
layer status --json
layer tree --json
layer explain common/util.c --json
```

Expected:

- `layer status --json` includes workspace path, output path, layer order, enabled/disabled state, branch, revision, and dirty/clean state.
- `layer tree --json` includes composed tree file paths and visible owner layer for each file.
- `layer explain <file> --json` includes visible layer, masked layers, source paths, revisions, and reason.
- JSON output is stable enough for a TypeScript extension to parse without scraping human text.

### Test 10: CLI entry point and Python module fallback

Both forms should work during development:

```bash
python3 -m layergit.cli status
layer status
```

Expected:

- `python3 -m layergit.cli ...` works before package installation.
- `layer ...` works after `python3 -m pip install -e .`.
- README examples use `layer ...` as the primary user-facing command.

### Test 10: Standard Git safety

From workspace root:

```bash
git status
```

Expected:

- If the workspace root is a Git repo, it tracks workspace-level files only.
- `.layer/` and `buildtree/` are ignored by default.

From `buildtree/`:

```bash
cd buildtree
git status
```

Expected:

- `buildtree/` is not a Git repo unless created by `layer export --init-git`.
- Users are directed to `layer status`, `layer explain`, or `layer -L <layer> git ...` for layer-aware operations.

### Test 12: Workspace `.gitignore` hygiene

```bash
layer init --output ./buildtree
cat .gitignore
```

Expected:

- `.gitignore` exists or was updated.
- It contains a LayerGit-managed block.
- `/.layer/` is ignored.
- `/buildtree/` or the configured output path is ignored.
- Existing user ignore rules are preserved.

### Test 13: Compose tracked files only by default

In a cached or fixture layer repo, create both a tracked source file and an untracked personal/test file:

```bash
echo tracked > tracked.txt
git add tracked.txt
git commit -m "Add tracked file"
echo local-note > personal-notes.txt
```

Then compose the workspace.

Expected:

- `tracked.txt` appears in `buildtree/`.
- `personal-notes.txt` does not appear in `buildtree/`.
- `personal-notes.txt` does not appear in `layer export` output by default.
- A future explicit `--include-untracked` option may change this behavior, but default composition is tracked-only.

---

## 21. Example quick-start for README

```bash
# Create a new layered workspace
layer init --output ./buildtree

# Add layers from bottom to top
# name is optional; without it, names are inferred from repo names.
layer add git@example.com/product.git product
layer add git@example.com/component-b.git
layer add git@example.com/component-c.git
layer add git@example.com/common.git common --top

# Check the composed workspace
layer status

# Optional: VS Code extension should show this same information via JSON-backed views
layer status --json
layer tree --json

# Explain one file
layer explain common/util.c

# Choose a different layer's version of one file
layer use common/util.c component-b

# Temporarily hide a layer without deleting it
layer disable component-c
layer enable component-c

# Pull all layers and rebuild the output tree
layer pull

# Export a normal repo snapshot
layer export ./merged-project --init-git
```

---

## 22. Suggested first milestone

Build the smallest CLI that can:

1. Initialize a workspace.
2. Add local Git repos as layers, including inferred default names when the optional name argument is omitted.
3. Create/update a safe workspace `.gitignore`.
4. Compose Git-tracked files into `buildtree/` by default.
5. Detect duplicate-path conflicts.
6. Apply default top-layer-wins masking for same-path overlaps.
7. Support `layer disable <layer>` and `layer enable <layer>` so layers can be hidden without deletion.
8. Support one file precedence command such as `layer use <file> <layer>`.
9. Explain file provenance.
10. Export the composed tree.
11. Provide `--json` output for `status`, `tree`, and `explain` so a VS Code extension can consume the CLI.

Avoid remote repo complexity initially if local repos are easier for testing. Add remote clone/pull after the local workflow works.

## 23. Suggested VS Code extension milestone

After the CLI can produce stable JSON, build the smallest VS Code extension that can:

1. Add a LayerGit icon to the Activity Bar.
2. Open a LayerGit View Container in the Primary Side Bar.
3. Detect `layer.yaml` in the current workspace.
4. Show an Initialize Workspace action if no LayerGit workspace exists.
5. Show a Layers Tree View based on `layer status --json`.
6. Show a Composed Tree View based on `layer tree --json`.
7. Run `layer explain <file> --json` for selected files.
8. Run enable/disable layer commands.
9. Run file precedence commands if the CLI supports them.
10. Refresh both views after CLI actions.
11. Support a configurable CLI command path through `layergit.command`.

Do not build a custom webview graph, drag-and-drop system, or Source Control provider in the first extension. Tree Views plus command actions are enough for a usable MVP.

---

## 24. Product pitch

Short pitch:

```text
LayerGit builds one clean, explainable source workspace from many Git repositories.
```

Longer pitch:

```text
Use LayerGit when your build system wants one source tree, but your codebase lives in several Git repos with overlapping shared files. Define layers, let the top layer win by default, temporarily disable layers without deleting them, adjust individual file precedence when needed, generate a deterministic build tree, explain where every file came from, and export the result as a normal repo when needed.
```
