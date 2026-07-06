# Alternatives and Related Projects

LayerGit is not meant to replace existing Git, build-system, or multi-repo
tools. If one of these fits your workflow cleanly, use it.

LayerGit is aimed at a narrower case: separate Git repositories need to appear
together as one generated source tree, and overlapping paths need clear
precedence, masking, provenance, and apply-back behavior.

| Tool | Multi-repo | Same logical tree | Direct overlay | Generated workspace | Explicit precedence/masking | File provenance | Per-file provider selection | Apply-back workflow | GUI / VS Code | Other VCS support | Best fit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| LayerGit | yes | yes | no | yes | yes | yes | yes | yes | yes | no, Git only | Compose separate Git repos into one explainable generated workspace. |
| [multigit](https://github.com/capr/multigit) | yes | yes | yes | no | partial | partial | unclear / direct Git workflow | direct Git workflow | not advertised | no, Git only | Lightweight direct overlay of multiple Git repos in one shared working tree. |
| [vcsh](https://github.com/RichiH/vcsh) | yes | yes | yes | no | limited; intended to avoid clobbering | Git-only | no | direct Git workflow | not advertised | no, Git only | Manage dotfiles/config sets from multiple Git repos in one working tree, often `$HOME`. |
| [Git submodules](https://git-scm.com/book/en/v2/Git-Tools-Submodules) | yes | subdirectories | no | no | no | Git tracks submodule commit | no | edit inside submodule repo | not advertised | no, Git only | Keep another Git repo inside a subdirectory while preserving separate history. |
| [git-subrepo](https://github.com/ingydotnet/git-subrepo) | yes | subdirectories | no | no | no | subrepo metadata | no | pull/push subrepo changes | not advertised | no, Git only | Vendor an external repo into a subdirectory with pull/push support. |
| [repo](https://gerrit.googlesource.com/git-repo/) | yes | workspace of repos | no | no | no | manifest pins repos | no | edit repos directly | not advertised | no, Git only | Manage many Git repositories from manifests, common in Android-style workflows. |
| [west](https://docs.zephyrproject.org/latest/develop/west/manifest.html) | yes | workspace of repos | no | no | no | manifest pins projects | no | edit repos directly | not advertised | no, Git only | Manage Zephyr-style multi-repo workspaces using `west.yml` manifests. |
| [vcstool](https://github.com/dirk-thomas/vcstool) | yes | workspace of repos | no | no | no | `.repos` file | no | edit repos directly | not advertised | yes | Import/export and run operations across multiple VCS repositories. |
| [myrepos / mr](https://myrepos.branchable.com/) | yes | separate repo folders | no | no | no | config tracks repos | no | edit repos directly | not advertised | yes | Run commands across many existing repositories. |
| Monorepo | not separate repos | yes | n/a | no | n/a | normal Git history | n/a | n/a | depends | depends | Put everything in one repository when shared history and ownership are acceptable. |
| Build-system dependencies | maybe | no / build-defined | no | no | no | build metadata | no | edit dependency repos directly | depends | depends | Let CMake, Bazel, package managers, or language tooling model dependencies directly. |

## Closest Related Tools

`multigit` is probably the closest related project because it overlays multiple
Git repositories into the same directory and describes those repositories as
layers. It is lightweight, stays close to direct Git workflows, and advertises
commands for tracked, untracked, and double-tracked files as well as
release/snapshot-related workflows.

LayerGit is different because it keeps layer repos under `.layer/cache/`, writes
a separate generated `buildtree/`, records visible/masked provenance, supports
ordered layer precedence, supports per-file provider selection with `layer use`,
and routes generated-tree edits back to source repos with `layer apply`.

`vcsh` is also close because it maintains several Git repositories in one
working tree, especially for dotfiles. Its target workflow is different:
directly managing config files in a shared working tree such as `$HOME`, rather
than generating a composed build or IDE tree.
