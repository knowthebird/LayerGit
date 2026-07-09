# Alternatives and Related Projects

LayerGit is not meant to replace existing Git, build-system, package-manager,
or multi-repo checkout tools. If one of those models fits cleanly, use it.

LayerGit is aimed at a narrower case: separate Git repositories need to appear
together as one generated source tree, and overlapping paths need clear
precedence, masking, provenance, and apply-back behavior.

Popularity numbers below were checked through the GitHub API on 2026-07-09
where a comparable GitHub repository was available. Treat them as approximate,
time-sensitive context, not as a quality ranking.

## Quick Comparison

| Tool / approach | Approx popularity | Main pitch | VCS support | Workspace model | Same-path overlap | Provenance | Apply-back | GUI/editor story | Best fit |
|---|---:|---|---|---|---|---|---|---|---|
| LayerGit | early alpha | Compose separate repos into one generated, explainable `buildtree/` | Git | isolated layer repos plus generated tree | top-layer-wins masking; explicit hide/use | visible, masked, hidden, stale, and untracked states | explicit `apply`, `apply --to`, and delete workflows | local VS Code extension plus CLI JSON | IDE/build workflows that require one source tree while repos stay separate |
| [`multigit`](https://github.com/capr/multigit) | 155 stars / 20 forks | Overlay multiple Git repos into one shared working tree | Git | direct shared working tree | can report double-tracked paths; one physical file exists at each path | Git-tracked-file visibility, not generated-tree provenance | normal Git edits in the shared tree | generic Git UI; no LayerGit-style provider UI advertised | lightweight direct overlay when users are comfortable managing overlaps directly |
| [`vcsh`](https://github.com/RichiH/vcsh) | 2,268 stars / 128 forks | Manage several Git repos in one working tree, commonly dotfiles | Git | direct shared working tree | intended to avoid clobbering, not provide source-layer precedence | Git-level history | normal Git edits | generic Git UI; no tool-specific VS Code UI advertised | dotfiles/config repos sharing `$HOME` or another tree |
| [Git submodules](https://git-scm.com/book/en/v2/Git-Tools-Submodules) | built into Git | Keep another Git repo at a fixed subdirectory | Git | parent repo with nested repo boundaries | subdirectory-based; not same-path overlay | parent pins submodule commit | edit and commit inside submodule | strong generic Git and VS Code support | dependencies that naturally live under path boundaries |
| [`git-subrepo`](https://github.com/ingydotnet/git-subrepo) | 3,591 stars / 289 forks | Vendor another repo into a subdirectory with pull/push support | Git | content copied into parent repo subdir with metadata | subdirectory-based; not same-path overlay | `.gitrepo` metadata plus normal Git history | pull/push subrepo changes | generic Git UI | vendor code that should live inside the parent repository |
| [`repo`](https://gerrit.googlesource.com/git-repo/) | 440 stars / 225 forks on GitHub mirror | Manage many Git checkouts from manifests | Git | workspace of separate repo folders | not a same-path composition model | manifest pins and checkout state | edit repos directly | generic Git UI per checkout; repo-specific GUI varies | Android/Gerrit-style manifest workspaces |
| [`west`](https://docs.zephyrproject.org/latest/develop/west/manifest.html) | 349 stars / 157 forks | Zephyr-oriented multi-repo workspace management | Git | workspace of separate repo folders | not a same-path composition model | `west.yml` manifest pins projects | edit repos directly | generic Git UI per checkout; Zephyr tooling exists | Zephyr and similar manifest-driven embedded projects |
| [`vcstool`](https://github.com/dirk-thomas/vcstool) | 499 stars / 103 forks | Import/export and run operations across repositories | Git, Mercurial, Subversion, Bazaar | workspace of separate repo folders | not a same-path composition model | `.repos` files record VCS, URL, and version | edit repos directly | generic SCM UI per checkout | ROS-style or mixed-VCS multi-repo management |
| [`myrepos / mr`](https://myrepos.branchable.com/) | TODO: verify current popularity | Run commands across many existing repositories | Git, SVN, Mercurial, Bazaar, CVS, Darcs, Fossil, Veracity, extensible | separate repo folders | not a same-path composition model | `.mrconfig` records repos and commands | edit repos directly | generic SCM UI per checkout | command orchestration across many repos |
| Monorepo | n/a | Put everything in one repository | depends | one repository | normal VCS conflicts inside one repo | normal repository history | normal commits | usually strong | shared history and ownership are acceptable |
| Build-system/package dependencies | n/a | Let the build or package tool model dependencies | ecosystem-specific | build-defined dependency graph | build-defined, not source overlay | lockfiles/manifests/build metadata | edit dependency repos directly | ecosystem-specific | dependencies do not need to appear as one source tree |

## When Not to Use LayerGit

Do not use LayerGit if:

- a monorepo is acceptable
- submodules or subrepos model your path boundaries cleanly
- your build system can consume dependencies directly
- you only need to run commands across many repo folders
- direct overlay into one shared working tree is simpler and good enough

LayerGit adds a generated tree, ownership metadata, and apply-back rules. That
overhead is only worth it when those checks make the workflow safer or easier to
explain.

## Same-path Overlaps

Same-path overlap is one of the main differences between LayerGit and direct
overlay tools.

In a direct shared working tree, there is only one physical file at a path such
as `common/util.c`. If more than one repository tracks that path, the tool can
report the overlap, but the user still has to manage which content is present in
the shared file.

LayerGit uses a generated `buildtree/` instead. If multiple enabled layers
provide the same path, the highest layer wins by default. Lower copies are
masked, not deleted, and LayerGit records the visible and masked providers.

Use:

```bash
layer overlaps
layer explain common/util.c
layer use common/util.c vendor-sdk
layer unuse common/util.c
```

to inspect or override the selected provider.

## Closest Related Tools

`multigit` is probably the closest related project because it overlays multiple
Git repositories into the same directory and describes those repositories as
layers. It is lighter and more direct than LayerGit: a shell script with no
dependencies, close to normal Git workflows, and commands for tracked,
untracked, and double-tracked files.

LayerGit differs by keeping layer repos under `.layer/cache/`, writing a
separate generated `buildtree/`, recording visible/masked/hidden provenance,
supporting ordered layer precedence, supporting per-file provider selection
with `layer use`, reporting active overlaps with `layer overlaps`, and routing
generated-tree edits back to source repos with `layer apply`.

`vcsh` is also close because it maintains several Git repositories in one
working tree, especially for dotfiles. Its target workflow is different:
directly managing config files in a shared working tree such as `$HOME`, rather
than generating a composed build or IDE tree.

## Popularity Sources

- [`RichiH/vcsh`](https://github.com/RichiH/vcsh)
- [`capr/multigit`](https://github.com/capr/multigit)
- [`ingydotnet/git-subrepo`](https://github.com/ingydotnet/git-subrepo)
- [`zephyrproject-rtos/west`](https://github.com/zephyrproject-rtos/west)
- [`dirk-thomas/vcstool`](https://github.com/dirk-thomas/vcstool)
- [`GerritCodeReview/git-repo`](https://github.com/GerritCodeReview/git-repo)

`myrepos / mr` is documented at <https://myrepos.branchable.com/>. I did not
find a comparable primary GitHub repository to cite for current star/fork
counts, so its popularity remains a TODO instead of an invented number.
