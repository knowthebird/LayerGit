# LayerGit Concepts

LayerGit composes separate Git repositories into one generated workspace. The
source repositories stay isolated, while `buildtree/` gives IDEs, build tools,
and legacy workflows a single tree to inspect.

## Workspace Files

- `layer.yaml` is the workspace recipe.
- `.layer/cache/<layer>/` contains isolated normal Git repos.
- `buildtree/` is generated output.
- `layer.lock.yaml` records exact layer commits after compose.
- `.layer/ownership.json` records visible and masked file provenance.
- `.layer/conflicts.json` records conflict and warning reports.

## Layer Stack

Layers are ordered from bottom to top. Higher enabled layers have higher
precedence.

```text
Layer 3: app            top / higher precedence
Layer 2: board-support
Layer 1: vendor-sdk     bottom / lower precedence
```

If more than one enabled layer provides the same buildtree path, the higher
layer wins by default. Lower providers are masked, not deleted.

Use:

```bash
layer status
layer overlaps
layer explain common/util.c
```

to inspect the stack and file provenance.

## Buildtree

`buildtree/` is generated output. Commit source changes from the layer repo that
owns the file, not from `buildtree/`.

LayerGit protects buildtree edits before operations that could silently discard
or reroute them. Use `layer apply`, `layer apply --to`, or
`layer compose --clean` when you intentionally want to keep, move, or discard
generated-tree edits.

## Source Caches

Layer repos live under `.layer/cache/<layer>/`. They are normal Git repositories.

You can inspect or commit them directly:

```bash
layer -L board-support git status
layer -L board-support git diff
layer -L board-support git commit -m "Fix board support"
```

LayerGit never commits or pushes for you.

## Mount Paths

By default, layers mount at `/`, so they compose into the root of `buildtree/`.
You can mount a whole layer under a buildtree subfolder:

```bash
layer add ../app app --mount /app
layer add ../docs docs --mount /docs
```

Mounts map the whole source repo root to that buildtree path. LayerGit does not
currently map only a source subdirectory.

## Masked vs Hidden

Masked means another provider exists, but a higher-precedence or explicitly
selected provider is visible. The masked file remains in its source layer.

Hidden means a path is intentionally suppressed from `buildtree/` by selection:

```bash
layer use legacy/unused.c board-support --hide
```

Clear explicit file selection with:

```bash
layer unuse legacy/unused.c
```

## Local Layers and Write Layer

Local layers are Git-backed repos created under `.layer/cache/`. They are useful
for experiments, local-only patches, or new files:

```bash
layer add --local local-edits
layer write local-edits
```

The write layer is where new unowned buildtree files go when you run
`layer apply --new` or `layer apply <new-file>`.

## Ownership Metadata

LayerGit writes `.layer/ownership.json` during compose. It records:

- visible provider
- masked providers
- hidden-by-selection paths
- selected/assigned layer
- source paths
- mount paths

Use JSON commands for tooling:

```bash
layer status --json
layer tree --json
layer overlaps --json
layer explain common/util.c --json
layer doctor --json
```
