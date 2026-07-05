from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from .composer import compose
from .errors import LayerError
from .exporter import export_workspace
from .gitops import ensure_local_layer_repo, layer_cache_path, remove_cache, sync_layer
from .manifest import (
    cache_dir,
    default_manifest,
    layer_dir,
    load_manifest,
    manifest_path,
    output_path,
    save_manifest,
)
from .merger import merge_layers
from .reports import (
    composed_tree,
    explain_file,
    explain_json,
    format_explain,
    format_status,
    layer_list,
    workspace_status,
)
from .selectors import insertion_index, select_layers
from .worktree import apply_buildtree_changes, buildtree_diff


PUBLIC_COMMANDS = {
    "help",
    "init",
    "add",
    "remove",
    "move",
    "enable",
    "disable",
    "status",
    "compose",
    "tree",
    "diff",
    "apply",
    "pull",
    "list",
    "git",
    "explain",
    "use",
    "unuse",
    "write",
    "merge",
    "export",
}


HELP_DESCRIPTION = """LayerGit composes layered Git repositories into one generated workspace."""


def format_layer_help(prog: str) -> str:
    return f"""usage: {prog} [-h] [-L <layer>] <command> [<args>]

LayerGit composes layered Git repositories into one generated workspace.

These are common LayerGit commands used in various situations:

Workspace:
  status              Show workspace and layer status
  compose             Regenerate the composed output tree
  compose --clean     Remove the output tree and regenerate from scratch
  tree                Show the composed tree
  diff                Show buildtree changes against owning layers
  apply               Copy buildtree changes back to layer repos

Layer management:
  init                Create a new LayerGit workspace
  add <repo> [name]   Add a repo as a layer
  add --local <name>  Add a local Git-backed layer
  remove <layer>      Remove a layer
  move <layer> <pos>  Move a layer to top, bottom, up, or down
  write <layer>       Set the default write layer
  enable <layer>      Enable a disabled layer
  disable <layer>     Disable a layer without deleting it

File provenance / selection:
  explain <file>      Explain which layer provides a file
  use <file> <layer>  Select which layer provides a file
  unuse <file>        Remove an explicit file selection

Git passthrough:
  -L, --layer <layer> Select a layer for layer-scoped commands
  git <args...>       Run git inside the selected layer

Examples:
  {prog} init --output ./buildtree
  {prog} init --output ./buildtree --no-base-layer
  {prog} add ../repo-a repoa
  {prog} add --local local-edits
  {prog} status
  {prog} move repoa up
  {prog} write local-edits
  {prog} compose
  {prog} compose --clean
  {prog} diff common/util.c
  {prog} apply common/util.c
  {prog} explain common/util.c
  {prog} use common/util.c compb
  {prog} -L local-edits git status
  {prog} -L compb git status
  {prog} -L compb git commit -m "Fix"

See '{prog} help <command>' to read about a specific command.
"""


class LayerArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        return format_layer_help(self.prog)


def build_parser(prog: str = "layer") -> argparse.ArgumentParser:
    parser = LayerArgumentParser(
        prog=prog,
        description=HELP_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-L", "--layer", dest="layer_selector", help="Select a layer for layer-scoped commands")
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=argparse.ArgumentParser,
    )

    init = sub.add_parser("init", help="Create a new layer workspace")
    init.add_argument("--output", default="./buildtree")
    init.add_argument("--no-base-layer", action="store_true")
    init.add_argument("--no-gitignore", action="store_true")
    init.add_argument("--update-gitignore", action="store_true")

    add = sub.add_parser("add", help="Add a source repo as a layer")
    add.add_argument("repo", nargs="?")
    add.add_argument("name", nargs="?")
    add.add_argument("--local", action="store_true")
    add.add_argument("--before")
    add.add_argument("--after")
    add.add_argument("--top", action="store_true")
    add.add_argument("--revision")
    add.add_argument("--no-sync", action="store_true")
    add.add_argument("--no-compose", action="store_true")

    remove = sub.add_parser("remove", help="Remove a layer from the manifest")
    remove.add_argument("selector")
    remove.add_argument("--keep-cache", action="store_true")
    remove.add_argument("--delete-cache", action="store_true")

    disable = sub.add_parser("disable", help="Disable a layer without deleting it")
    disable.add_argument("selector")

    enable = sub.add_parser("enable", help="Re-enable a disabled layer")
    enable.add_argument("selector")

    move = sub.add_parser(
        "move",
        usage="%(prog)s <layer> <top|bottom|up|down|before|after> [target-layer]",
        description=(
            "Move one layer in the layer stack.\n\n"
            "The command is `layer move <layer> <position>`.\n"
            "For example, `layer move layera up` moves layera one step toward the top."
        ),
        epilog=(
            "Examples:\n"
            "  layer move layera up\n"
            "  layer move layera down\n"
            "  layer move layera top\n"
            "  layer move layera bottom\n"
            "  layer move layera before base\n"
            "  layer move layera after component-b"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Move a layer in the stack",
    )
    move.add_argument("selector", metavar="layer")
    move.add_argument("position", metavar="position", choices=("top", "bottom", "up", "down", "before", "after"))
    move.add_argument("target", metavar="target-layer", nargs="?")

    status = sub.add_parser("status", help="Show workspace status")
    status.add_argument("--json", action="store_true")

    list_cmd = sub.add_parser("list", help="List configured layers")
    list_cmd.add_argument("--json", action="store_true")

    tree = sub.add_parser("tree", help="List composed output tree files")
    tree.add_argument("--json", action="store_true")

    diff = sub.add_parser(
        "diff",
        description=(
            "Show changes made in the composed output tree compared with the "
            "owning layer cache repositories."
        ),
        help="Show buildtree changes against owning layers",
    )
    diff.add_argument("path", nargs="?")
    diff.add_argument("--layer", dest="target_layer")
    diff.add_argument("--new", action="store_true", help="show only new unowned buildtree files")
    diff.add_argument("--json", action="store_true")

    apply = sub.add_parser(
        "apply",
        description=(
            "Copy changes from the composed output tree back into layer cache "
            "repositories. Git still handles add, commit, branch, merge, push, and history."
        ),
        help="Copy buildtree changes back to layer repos",
    )
    apply.add_argument("path", nargs="?")
    apply.add_argument("--all", action="store_true", help="apply all modified owned files and new unowned files")
    apply.add_argument("--new", action="store_true", help="apply only new unowned buildtree files")
    apply.add_argument("--layer", dest="target_layer", help="apply only changes targeting this layer")
    apply.add_argument("--dry-run", action="store_true", help="show what would be applied without copying files")
    apply.add_argument("--delete", action="store_true", help="apply deleted buildtree files by deleting layer source files")
    apply.add_argument("--yes", action="store_true", help="accepted for non-interactive workflows")

    compose_cmd = sub.add_parser(
        "compose",
        description=(
            "Regenerate the composed output tree from cached layer repositories.\n\n"
            "By default, LayerGit updates LayerGit-owned files and preserves untracked "
            "buildtree files such as compiler outputs or IDE artifacts. Use --clean "
            "to remove the output tree first and regenerate only composed files."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Rebuild the composed output tree",
    )
    compose_cmd.add_argument("--json", action="store_true")
    compose_cmd.add_argument(
        "--clean",
        action="store_true",
        help="remove the output tree before composing, including untracked buildtree files",
    )

    pull = sub.add_parser("pull", help="Pull one or more layers and recompose")
    pull.add_argument("selector", nargs="?", default="all")
    pull.add_argument("--no-compose", action="store_true")
    pull.add_argument(
        "--no-fetch",
        action="store_true",
        help="Re-read cached layer repos and recompose without fetching remotes",
    )

    git = sub.add_parser("git", help="Run Git inside the selected layer")
    git.add_argument("git_args", nargs=argparse.REMAINDER)

    explain = sub.add_parser("explain", help="Explain file provenance")
    explain.add_argument("path")
    explain.add_argument("--json", action="store_true")

    use_file = sub.add_parser("use", help="Select which layer should provide a file")
    use_file.add_argument("path")
    use_file.add_argument("layer")

    unuse = sub.add_parser("unuse", help="Remove an explicit file selection")
    unuse.add_argument("path")

    write = sub.add_parser("write", help="Set the default write layer")
    write.add_argument("selector")

    merge = sub.add_parser("merge", help="Flatten selected layers into a new layer")
    merge.add_argument("selector")
    merge.add_argument("--name", required=True)
    merge.add_argument("--init-git", action="store_true")
    merge.add_argument("--with-provenance", action="store_true")

    export = sub.add_parser("export", help="Export composed tree as a normal directory")
    export.add_argument("destination")
    export.add_argument("--init-git", action="store_true")
    export.add_argument("--with-provenance", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    prog = program_name()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "help":
        return cmd_help(prog, argv[1:])
    invalid = invalid_command(argv)
    if invalid:
        print(f"{prog}: '{invalid}' is not a {prog} command. See '{prog} --help'.", file=sys.stderr)
        return 1

    parser = build_parser(prog)
    args = parser.parse_args(argv)
    root = Path.cwd()

    try:
        if args.command == "init":
            return cmd_init(root, args)
        if args.command == "add":
            return cmd_add(root, args)
        if args.command == "remove":
            return cmd_remove(root, args)
        if args.command == "disable":
            return cmd_set_enabled(root, args.selector, enabled=False)
        if args.command == "enable":
            return cmd_set_enabled(root, args.selector, enabled=True)
        if args.command == "move":
            return cmd_move_layer(root, args)
        if args.command == "status":
            return cmd_status(root, args)
        if args.command == "list":
            return cmd_list(root, args)
        if args.command == "tree":
            return cmd_tree(root, args)
        if args.command == "diff":
            return cmd_diff(root, args)
        if args.command == "apply":
            return cmd_apply(root, args)
        if args.command == "compose":
            return cmd_compose(root, args)
        if args.command == "pull":
            return cmd_pull(root, args)
        if args.command == "git":
            return cmd_git(root, args)
        if args.command == "explain":
            return cmd_explain(root, args)
        if args.command == "use":
            return cmd_use_file(root, args)
        if args.command == "unuse":
            return cmd_unuse_file(root, args)
        if args.command == "write":
            return cmd_write_layer(root, args)
        if args.command == "merge":
            return cmd_merge(root, args)
        if args.command == "export":
            return cmd_export(root, args)
    except LayerError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_help(prog: str, args: list[str]) -> int:
    if not args:
        print(format_layer_help(prog), end="")
        return 0
    command = args[0]
    if command not in PUBLIC_COMMANDS or command == "help":
        print(f"{prog}: '{command}' is not a {prog} command. See '{prog} --help'.", file=sys.stderr)
        return 1
    parser = build_parser(prog)
    try:
        parser.parse_args([command, "--help"])
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def program_name() -> str:
    return "layergit" if Path(sys.argv[0]).name == "layergit" else "layer"


def invalid_command(argv: list[str]) -> str | None:
    if not argv or argv in (["-h"], ["--help"]):
        return None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("-L", "--layer"):
            index += 2
            continue
        if item.startswith("-"):
            return None
        return None if item in PUBLIC_COMMANDS else item
    return None


def cmd_init(root: Path, args: argparse.Namespace) -> int:
    if manifest_path(root).exists():
        raise LayerError("layer.yaml already exists")
    manifest = default_manifest(args.output, base_layer=not args.no_base_layer)
    save_manifest(root, manifest)
    layer_dir(root).mkdir(exist_ok=True)
    cache_dir(root).mkdir(parents=True, exist_ok=True)
    if not args.no_base_layer:
        ensure_local_layer_repo(root, "workspace-base")
    output_path(root, manifest).mkdir(parents=True, exist_ok=True)
    if not args.no_gitignore:
        ensure_gitignore(root, args.output)
    print("Initialized layer workspace")
    return 0


def cmd_add(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    layers = manifest["layers"]
    if args.local:
        if args.repo is None or args.name is not None:
            raise LayerError("Use `layer add --local <name>` to create a local layer")
        explicit_name = True
        name = args.repo
        repo = None
        kind = "local"
    else:
        if args.repo is None:
            raise LayerError("add requires a repo path or --local <name>")
        explicit_name = args.name is not None
        name = args.name or infer_layer_name(args.repo, layers)
        repo = args.repo
        kind = "git"
    if any(layer.get("name") == name for layer in layers):
        if explicit_name:
            raise LayerError(f"Layer `{name}` already exists")
        name = unique_layer_name(name, layers)
    layer = {"name": name, "kind": kind, "enabled": True}
    if repo:
        layer["repo"] = repo
    if args.revision:
        if kind == "local":
            raise LayerError("--revision is only valid for Git-backed repo layers")
        layer["revision"] = args.revision

    index = insertion_index(layers, before=args.before, after=args.after, top=args.top)
    layers.insert(index, layer)
    save_manifest(root, manifest)

    if kind == "local":
        ensure_local_layer_repo(root, name)
    elif not args.no_sync:
        sync_layer(root, layer, clone_only=True)
    if not args.no_compose:
        result = compose(root, manifest, sync=False)
        print_compose_result(result)
        print(f"Added layer {index + 1}: {name}")
        print(f"Kind: {kind}")
        if repo:
            print(f"Source: {repo}")
        return 1 if result["conflicts"] else 0
    print(f"Added layer {index + 1}: {name}")
    print(f"Kind: {kind}")
    if repo:
        print(f"Source: {repo}")
    return 0


def cmd_remove(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    indexes = select_layers(manifest["layers"], args.selector)
    if len(indexes) != 1:
        raise LayerError("remove expects exactly one layer")
    layer = manifest["layers"].pop(indexes[0])
    save_manifest(root, manifest)
    if args.delete_cache:
        remove_cache(root, layer["name"])
    result = compose(root, manifest, sync=False)
    print_compose_result(result)
    return 1 if result["conflicts"] else 0


def cmd_set_enabled(root: Path, selector: str, *, enabled: bool) -> int:
    manifest = load_manifest(root)
    indexes = select_layers(manifest["layers"], selector)
    if len(indexes) != 1:
        raise LayerError("enable/disable expects exactly one layer")
    layer = manifest["layers"][indexes[0]]
    layer["enabled"] = enabled
    save_manifest(root, manifest)
    result = compose(root, manifest, sync=False)
    action = "Enabled" if enabled else "Disabled"
    print(f"{action} layer {indexes[0] + 1}: {layer['name']}")
    print_compose_result(result)
    return 1 if result["conflicts"] else 0


def cmd_move_layer(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    layers = manifest["layers"]
    indexes = select_layers(layers, args.selector)
    if len(indexes) != 1:
        raise LayerError("layer movement expects exactly one layer")

    index = indexes[0]
    layer = layers.pop(index)
    if args.position == "up":
        new_index = min(index + 1, len(layers))
    elif args.position == "down":
        new_index = max(index - 1, 0)
    elif args.position == "top":
        new_index = len(layers)
    elif args.position == "bottom":
        new_index = 0
    elif args.position == "before":
        if not args.target:
            raise LayerError("move before requires a target layer")
        new_index = select_layers(layers, args.target)[0]
    elif args.position == "after":
        if not args.target:
            raise LayerError("move after requires a target layer")
        new_index = select_layers(layers, args.target)[-1] + 1
    else:
        raise LayerError(f"Unknown layer movement position: {args.position}")

    layers.insert(new_index, layer)
    save_manifest(root, manifest)
    result = compose(root, manifest, sync=False)
    print(f"Moved layer {layer['name']} from {index + 1} to {new_index + 1}")
    print_compose_result(result)
    return 1 if result["conflicts"] else 0


def cmd_status(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    status = workspace_status(root, manifest)
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(format_status(status))
    return 0


def cmd_list(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    data = layer_list(root, manifest)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(format_status({"layers": data["layers"], "composed_tree": {"output": data["output"], "visible_files": 0, "masked_files": 0, "conflicts": 0, "warnings": 0}, "conflicts": [], "warnings": [], "modified_files": []}).split("\n\n")[0])
    return 0


def cmd_tree(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    data = composed_tree(root, manifest)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for item in data["files"]:
            owner = (
                f"hidden by {item.get('selectedLayer') or 'selection'}"
                if item.get("hidden")
                else item.get("visibleLayer") or "-"
            )
            print(f"{item['path']} -> {owner}")
    return 0


def cmd_diff(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    data = buildtree_diff(
        root,
        manifest,
        path=args.path,
        layer=args.target_layer,
        new_only=args.new,
    )
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(format_buildtree_diff(data))
    return 0


def cmd_apply(root: Path, args: argparse.Namespace) -> int:
    if not (args.path or args.all or args.new or args.target_layer):
        raise LayerError("apply requires a path, --all, --new, or --layer <layer>")
    manifest = load_manifest(root)
    diff = buildtree_diff(
        root,
        manifest,
        path=args.path,
        layer=args.target_layer,
        new_only=args.new,
    )
    result = apply_buildtree_changes(
        root,
        manifest,
        diff,
        include_deleted=args.delete,
        dry_run=args.dry_run,
    )
    print(format_apply_result(result, dry_run=args.dry_run))
    return 0


def cmd_compose(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    result = compose(root, manifest, sync=False, clean=args.clean)
    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "ownership"}, indent=2, sort_keys=True))
    else:
        print_compose_result(result)
    return 1 if result["conflicts"] else 0


def cmd_pull(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    indexes = select_layers(
        manifest["layers"],
        args.selector,
        enabled_only_for_default=True,
    )
    if not args.no_fetch:
        for index in indexes:
            sync_layer(root, manifest["layers"][index])
    if args.no_compose:
        action = "Checked" if args.no_fetch else "Pulled"
        print(f"{action} {len(indexes)} layer(s)")
        return 0
    result = compose(root, manifest, sync=False)
    print_compose_result(result)
    return 1 if result["conflicts"] else 0


def cmd_git(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    if not args.layer_selector:
        raise LayerError("git requires a layer. Use: layer -L <layer> git <git-args>")
    if not args.git_args:
        raise LayerError("Missing git command")
    indexes = select_layers(manifest["layers"], args.layer_selector)
    if len(indexes) != 1:
        raise LayerError("git requires exactly one layer")
    layer = manifest["layers"][indexes[0]]
    cache = layer_cache_path(root, layer["name"])
    result = subprocess.run(["git", *args.git_args], cwd=cache)
    return result.returncode


def cmd_explain(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    entry = explain_file(root, args.path, manifest)
    if args.json:
        print(json.dumps(explain_json(root, args.path, manifest) or {}, indent=2, sort_keys=True))
    else:
        print(format_explain(args.path, entry))
    return 0 if entry else 1


def cmd_use_file(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    layers = manifest.get("layers", [])
    matching = [layer for layer in layers if layer.get("name") == args.layer]
    if not matching:
        raise LayerError(f"Unknown layer `{args.layer}`")
    layer = matching[0]
    if not layer.get("enabled", True):
        raise LayerError(f"Layer `{args.layer}` is disabled and cannot be selected for a file")

    manifest.setdefault("file_selection", {})[args.path] = {"layer": args.layer}
    file_precedence = manifest.get("file_precedence")
    if isinstance(file_precedence, dict):
        file_precedence.pop(args.path, None)
        if not file_precedence:
            manifest.pop("file_precedence", None)
    save_manifest(root, manifest)
    result = compose(root, manifest, sync=False)
    print(f"Selected layer {args.layer} for {args.path}")
    print_compose_result(result)
    return 1 if result["conflicts"] else 0


def cmd_unuse_file(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    changed = False
    file_selection = manifest.get("file_selection")
    if isinstance(file_selection, dict) and args.path in file_selection:
        file_selection.pop(args.path, None)
        changed = True
        if not file_selection:
            manifest.pop("file_selection", None)
    file_precedence = manifest.get("file_precedence")
    if isinstance(file_precedence, dict) and args.path in file_precedence:
        file_precedence.pop(args.path, None)
        changed = True
        if not file_precedence:
            manifest.pop("file_precedence", None)
    if not changed:
        print(f"No explicit file selection for {args.path}")
        return 0
    save_manifest(root, manifest)
    result = compose(root, manifest, sync=False)
    print(f"Removed explicit file selection for {args.path}")
    print_compose_result(result)
    return 1 if result["conflicts"] else 0


def cmd_write_layer(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    indexes = select_layers(manifest["layers"], args.selector)
    if len(indexes) != 1:
        raise LayerError("write expects exactly one layer")
    layer = manifest["layers"][indexes[0]]
    manifest.setdefault("workspace", {})["write_layer"] = layer["name"]
    save_manifest(root, manifest)
    suffix = " (local)" if layer.get("kind") == "local" else ""
    print(f"Write layer: {layer['name']}{suffix}")
    return 0


def cmd_merge(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    indexes = select_layers(manifest["layers"], args.selector)
    manifest = merge_layers(
        root,
        manifest,
        indexes,
        args.name,
        init_git=args.init_git,
        with_provenance=args.with_provenance,
    )
    save_manifest(root, manifest)
    result = compose(root, manifest, sync=False)
    print_compose_result(result)
    return 1 if result["conflicts"] else 0


def cmd_export(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    export_workspace(
        root,
        manifest,
        Path(args.destination),
        init_git=args.init_git,
        with_provenance=args.with_provenance,
    )
    print(f"Exported composed tree to {args.destination}")
    return 0


def print_compose_result(result: dict) -> None:
    print(
        f"Composed {result['visible_files']} visible file(s), "
        f"{result['masked_files']} masked file(s), "
        f"{len(result['conflicts'])} conflict(s), "
        f"{len(result.get('warnings', []))} warning(s)"
    )
    for warning in result.get("warnings", []):
        print("")
        print(f"WARNING: {warning['message']}")
        print_conflict_like_finding(warning)
    for conflict in result["conflicts"]:
        print("")
        print(f"ERROR: {conflict['message']}")
        print_conflict_like_finding(conflict)


def print_conflict_like_finding(finding: dict) -> None:
    print("")
    print(f"{finding['path']} appears as:")
    for provider in finding.get("providers", []):
        print(
            f"  layer {provider['layer_index']} {provider['layer']}: "
            f"{provider['source_path']}"
        )
    print("")
    print("Options:")
    print(f"  1. Use layer use {finding['path']} <layer> to choose a visible owner")
    print("  2. Exclude one source file from a layer")
    print("  3. Rename/remap one file")
    print("  4. Change duplicate_basename_policy to warn if this build system allows it")


def format_buildtree_diff(data: dict[str, list[dict[str, str | None]]]) -> str:
    lines: list[str] = []
    append_diff_section(lines, "Modified:", data.get("modified", []), target_key="layer")
    append_diff_section(lines, "New:", data.get("new", []), target_key="write_layer", label="write layer")
    append_diff_section(lines, "Deleted:", data.get("deleted", []), target_key="layer")
    append_diff_section(lines, "Stale owned:", data.get("stale", []), target_key="layer")
    if not lines:
        return "No buildtree changes."
    return "\n".join(lines)


def append_diff_section(
    lines: list[str],
    title: str,
    items: list[dict[str, str | None]],
    *,
    target_key: str,
    label: str | None = None,
) -> None:
    if not items:
        return
    if lines:
        lines.append("")
    lines.append(title)
    for item in items:
        target = item.get(target_key) or "unassigned"
        if label and item.get(target_key):
            lines.append(f"  {item['path']} -> {label} {target}")
        else:
            lines.append(f"  {item['path']} -> {target}")


def format_apply_result(data: dict[str, list[dict[str, str | None]]], *, dry_run: bool) -> str:
    lines: list[str] = []
    verb = "Would apply" if dry_run else "Applied"
    append_apply_section(lines, f"{verb} modified:", data.get("modified", []), target_key="layer")
    append_apply_section(lines, f"{verb} new:", data.get("new", []), target_key="write_layer", label="write layer")
    append_apply_section(lines, f"{verb} deleted:", data.get("deleted", []), target_key="layer")
    append_apply_section(
        lines,
        "Deleted buildtree file not applied:",
        data.get("skipped_deleted", []),
        target_key="layer",
    )
    if data.get("skipped_deleted"):
        lines.extend(["", "Use:", "  layer apply --delete <path>"])
    if not lines:
        return "No buildtree changes to apply."
    return "\n".join(lines)


def append_apply_section(
    lines: list[str],
    title: str,
    items: list[dict[str, str | None]],
    *,
    target_key: str,
    label: str | None = None,
) -> None:
    if not items:
        return
    if lines:
        lines.append("")
    lines.append(title)
    for item in items:
        target = item.get(target_key) or "unassigned"
        if label and item.get(target_key):
            lines.append(f"  {item['path']} -> {label} {target}")
        else:
            lines.append(f"  {item['path']} -> {target}")


def ensure_gitignore(root: Path, output: str) -> None:
    entries = ["/.layer/"]
    output_entry = gitignore_output_entry(root, output)
    if output_entry:
        entries.append(output_entry)
    entries.extend(["/.layer-exports/", "/merged-project/", "/merged-*/"])
    path = root / ".gitignore"
    existing = path.read_text() if path.exists() else ""
    block = "\n".join(
        [
            "# BEGIN LayerGit",
            "# Generated cache of layer repositories",
            entries[0],
            "",
            "# Generated composed output tree",
            entries[1],
            "",
            "# Optional local exports/scratch outputs",
            *entries[2:],
            "# END LayerGit",
        ]
    )
    pattern = re.compile(r"# BEGIN LayerGit\n.*?\n# END LayerGit", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub(block, existing)
    else:
        separator = "\n\n" if existing and not existing.endswith("\n\n") else ""
        updated = f"{existing}{separator}{block}\n"
    path.write_text(updated if updated.endswith("\n") else updated + "\n")


def gitignore_output_entry(root: Path, output: str) -> str | None:
    output_path_value = Path(output)
    if output_path_value.is_absolute():
        try:
            output_path_value = output_path_value.relative_to(root)
        except ValueError:
            return None
    entry = output_path_value.as_posix().removeprefix("./").strip("/")
    if not entry or entry == ".":
        return None
    return f"/{entry}/"


def infer_layer_name(repo: str, layers: list[dict]) -> str:
    raw = repo.rstrip("/").split("/")[-1]
    if ":" in raw:
        raw = raw.split(":")[-1]
    if raw.endswith(".git"):
        raw = raw[:-4]
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).lower()
    normalized = re.sub(r"-+", "-", normalized).strip("-._")
    return unique_layer_name(normalized or "layer", layers)


def unique_layer_name(base: str, layers: list[dict]) -> str:
    existing = {layer.get("name") for layer in layers}
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


if __name__ == "__main__":
    raise SystemExit(main())
