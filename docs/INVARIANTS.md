# LayerGit Safety Invariants

LayerGit is designed around these safety rules. These are implementation
promises that should be covered by real-Git tests when behavior changes.

## Source Repo Isolation

- Layer repos under `.layer/cache/<layer>/` are the source of truth.
- `buildtree/` is generated output.
- `layer compose` never deletes source files from layer repos.
- Disabling, removing, or reordering a layer never deletes source files from
  layer repos.
- Masked lower-layer files are never deleted when a visible file is changed or
  deleted.
- Source repos are changed only by explicit apply/delete operations or by
  direct user-run Git commands inside a layer repo.

## No Automatic Commits Or Pushes

- LayerGit never commits for the user during normal layer/apply/delete
  workflows.
- LayerGit never pushes for the user.
- Git staging only happens where documented.
- Export and merge commands may initialize standalone Git repositories only
  when the user explicitly requests that mode with `--init-git`.

## Apply Behavior

- `layer apply <path>` writes only to the current owning layer.
- `layer apply <path> --to <layer>` writes only to the selected target layer.
- `layer apply <path> --to <layer>` assigns that path to the selected layer.
- `layer apply <path> --delete` deletes only from the current visible owning
  layer.
- `layer apply <path> --delete` does not delete masked lower-layer files.
- `layer apply` fails rather than guessing when ownership is stale or
  ambiguous.

## Layer Selection

- `layer use <path> <layer>` selects an existing layer copy for a file.
- If the selected layer does not contain the path, plain `layer use` fails
  instead of guessing whether to hide or copy.
- `layer use <path> <layer> --hide` intentionally suppresses inherited
  lower-layer files without deleting source files.

## Staging

- Existing tracked file modifications are not staged by default.
- Existing tracked file modifications are staged when `--stage` is used.
- New files created by `layer apply <path> --to <layer>` are staged by default
  unless `--no-stage` is used.
- Source deletes are staged by default unless `--no-stage` is used.
- LayerGit stages only files it changed.
- LayerGit does not stage unrelated dirty files.

## Dry Run

- Dry-run commands must not modify `buildtree/`.
- Dry-run commands must not modify layer cache repos.
- Dry-run commands must not modify ownership metadata.
- Dry-run commands must not modify the Git index.

## Dirty Buildtree Protection

- Dirty `buildtree/` edits must not be silently discarded.
- Dirty `buildtree/` edits must not be silently rerouted to another layer.
- Users should be told to apply, apply with `--to`, or intentionally recompose.

## Path Safety

- LayerGit rejects paths that escape the workspace.
- LayerGit rejects paths that escape layer cache repos.
- Mount-path mapping must not permit traversal outside the target layer.
- A layer mounted at `/app` maps `buildtree/app/foo.c` to
  `.layer/cache/<layer>/foo.c`, not `.layer/cache/<layer>/app/foo.c`.

## JSON Contracts

- `layer status --json` exposes stable workspace, layer, composed-tree, and
  warning/conflict fields used by the VS Code extension.
- `layer tree --json` exposes visible, masked, hidden, stale, and untracked
  file states.
- `layer explain <path> --json` explains visible, masked, hidden, stale, and
  unowned states.
- `layer doctor --json` exposes `status`, `checks`, and `summary`.
- Normal JSON commands must not include traceback-style noise.

## Verification

The Python test suite uses real temporary Git repositories for CLI workflows.
Invariant tests cover source-repo isolation, masked lower-layer preservation,
dry-run behavior, dirty buildtree protection, mount-path mapping, apply/delete
staging behavior, and CLI JSON compatibility.
