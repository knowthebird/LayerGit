# Alternatives and Related Projects

LayerGit is not meant to replace existing Git, build-system, or multi-repo
tools. If one of these fits your workflow cleanly, use it.

LayerGit is aimed at a narrower case: separate Git repositories need to appear
together as one generated source tree, and overlapping paths need clear
precedence, masking, provenance, and apply-back behavior.

LayerGit is focused on Git-only source-tree composition into a generated,
explainable `buildtree/`. Layer repos stay isolated under `.layer/cache/` as
separate sources of truth. That overhead is useful when users need to know which
repo provided a visible file, which lower-layer files are masked, and which repo
should receive an edit made in the generated workspace.

Some alternatives are better fits for lightweight direct overlays, dotfiles,
manifest-based multi-repo checkout, or multi-VCS repository management.

## Feature Matrix

| Tool / approach | VCS support | Multi-repo | Same logical tree | Direct overlay | Generated workspace | Same-path overlap handling | Provenance / explainability | Per-file provider selection | Apply-back workflow | GUI / VS Code support | Best fit |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | --- | --- |
| LayerGit | Git only | yes | yes | no | yes, `buildtree/` | Deterministic top-layer-wins masking; lower files are masked, not deleted | Visible/masked provenance with `layer explain` | yes, `layer use` | yes, `layer apply` routes `buildtree/` edits back to layer repos | yes, local VS Code extension | Explainable generated workspace for IDE/build workflows where repos must remain separate. |
| [multigit](https://github.com/capr/multigit) | Git only | yes | yes | yes | no | Can list files tracked by multiple repos; shared tree still has one physical file at that path | partial; Git-tracked-file visibility, not LayerGit-style visible/masked provenance | direct Git workflow | direct Git workflow | generic Git UI likely works; tool-specific VS Code support not advertised | Lightweight direct overlay of multiple Git repos into one working tree. |
| [vcsh](https://github.com/RichiH/vcsh) | Git only | yes | yes | yes | no | Intended to avoid clobbering shared files rather than provide source-layer precedence | Git-level only | no | direct Git workflow | generic Git UI likely works; tool-specific VS Code support not advertised | Dotfiles/config repositories sharing `$HOME` or another working tree. |
| [Git submodules](https://git-scm.com/book/en/v2/Git-Tools-Submodules) | Git only | yes | subdirectories | no | no | Repos live at path boundaries, so same-path overlays are not the model | Git records a submodule commit in the parent repo | no | edit inside submodule repo | yes, common Git GUI / VS Code Git workflows | Keep another Git repo inside a subdirectory while preserving separate history. |
| [git-subrepo](https://github.com/ingydotnet/git-subrepo) | Git only | yes | subdirectories | no | no | Subdirectory-based; not a same-path layer overlay model | `.gitrepo` metadata and normal Git history | no | pull/push subrepo changes | generic Git UI works for the parent repo; tool-specific VS Code support not advertised | Vendor another repo into a subdirectory with pull/push support. |
| [repo](https://gerrit.googlesource.com/git-repo/) | Git only | yes | workspace of repos | no | no | Manages repo checkouts; not focused on overlapping file paths in one generated tree | manifest pins and repo state | no | edit repos directly | generic Git UI works per checkout; repo-specific GUI varies | Manage many Git repositories from manifests, common in Android-style workflows. |
| [west](https://docs.zephyrproject.org/latest/develop/west/manifest.html) | Git only | yes | workspace of repos | no | no | Manifest workspace model; not focused on same-path source overlays | `west.yml` manifest pins projects | no | edit repos directly | generic Git UI works per checkout; Zephyr tooling exists | Manage Zephyr-style multi-repo workspaces using `west.yml` manifests. |
| [vcstool](https://github.com/dirk-thomas/vcstool) | Git, Mercurial, Subversion, Bazaar | yes | workspace of repos | no | no | Workspace management; not same-path layer composition | `.repos` export/import records VCS type, URL, and version | no | edit repos directly | generic SCM UI works per checkout when available; tool-specific VS Code support not advertised | Import/export and run operations across multiple VCS repositories. |
| [myrepos / mr](https://myrepos.branchable.com/) | Git, SVN/Subversion, Mercurial, Bazaar, CVS, Darcs, Fossil, Veracity, and extensible | yes | separate repo folders | no | no | Multi-repo command runner; not same-path layer composition | `.mrconfig` records repositories and commands | no | edit repos directly | generic SCM UI works per checkout when available; tool-specific VS Code support not advertised | Run commands across many existing repositories. |
| Monorepo | depends | not separate repos | yes | n/a | no | Same-path conflicts are normal VCS conflicts within one repo | normal repository history | n/a | n/a | depends on VCS and host tooling | Put everything in one repository when shared history and ownership are acceptable. |
| Build-system dependencies | depends on build/package tool | maybe | no / build-defined | no | no | Build-defined layout; not a generated source overlay | build metadata and lockfiles | no | edit dependency repos directly | depends on ecosystem | Let CMake, Bazel, package managers, or language tooling model dependencies directly. |

The `GUI / VS Code support` column mixes two different things: generic Git UI
support, which is strong for ordinary Git layouts such as submodules, and
tool-specific UI for the comparison tool itself. A generic Git UI can be useful
without providing LayerGit-style composition, masking, provenance, or provider
selection.

## Same-path Overlaps

This is one of the main differences between LayerGit and direct-overlay tools.

In a direct shared working tree, such as multigit's model, there is only one
physical file at a path like `common/util.c`. If more than one repository tracks
that path, the shared working tree can expose that overlap and report it as
double-tracked, but the user still has to manage which content is present in the
shared file.

LayerGit uses a generated `buildtree/` instead. If multiple layers provide the
same path, the highest enabled layer wins by default. Lower copies are masked,
not deleted, and LayerGit records visible and masked provenance.
`layer explain <path>` shows which layer provided the visible file, and
`layer use <path> <layer>` can override the default provider for one path.
`layer overlaps` lists paths currently provided by more than one enabled layer.
Because the composed tree is generated, LayerGit can also mount whole layer
repos under buildtree subfolders while keeping the source repos isolated.

## Closest Related Tools

`multigit` is probably the closest related project because it overlays multiple
Git repositories into the same directory and describes those repositories as
layers. It is lighter and more direct than LayerGit: a shell script with no
dependencies, close to normal Git workflows, and commands for tracked,
untracked, and double-tracked files as well as release/snapshot-related
workflows. It may be a better fit when the user wants multiple Git repos
overlaid into one physical working tree and is comfortable managing overlapping
tracked files directly.

LayerGit is different because it keeps layer repos under `.layer/cache/`, writes
a separate generated `buildtree/`, records visible/masked provenance, supports
ordered layer precedence, supports per-file provider selection with `layer use`,
reports active overlaps with `layer overlaps`, and routes generated-tree edits
back to source repos with `layer apply`.

`vcsh` is also close because it maintains several Git repositories in one
working tree, especially for dotfiles. Its target workflow is different:
directly managing config files in a shared working tree such as `$HOME`, rather
than generating a composed build or IDE tree.
