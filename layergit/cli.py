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
from .gitops import layer_cache_path, remove_cache, sync_layer
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
    "pull",
    "list",
    "git",
    "explain",
    "use",
    "unuse",
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

Layer management:
  init                Create a new LayerGit workspace
  add <repo> [name]   Add a repo as a layer
  remove <layer>      Remove a layer
  move <layer> <pos>  Move a layer to top, bottom, up, or down
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
  {prog} add ../repo-a repoa
  {prog} status
  {prog} move repoa up
  {prog} compose
  {prog} compose --clean
  {prog} explain common/util.c
  {prog} use common/util.c compb
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
    init.add_argument("--no-gitignore", action="store_true")
    init.add_argument("--update-gitignore", action="store_true")

    add = sub.add_parser("add", help="Add a source repo as a layer")
    add.add_argument("repo")
    add.add_argument("name", nargs="?")
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
    save_manifest(root, default_manifest(args.output))
    layer_dir(root).mkdir(exist_ok=True)
    cache_dir(root).mkdir(parents=True, exist_ok=True)
    output_path(root, default_manifest(args.output)).mkdir(parents=True, exist_ok=True)
    if not args.no_gitignore:
        ensure_gitignore(root, args.output)
    print("Initialized layer workspace")
    return 0


def cmd_add(root: Path, args: argparse.Namespace) -> int:
    manifest = load_manifest(root)
    layers = manifest["layers"]
    explicit_name = args.name is not None
    name = args.name or infer_layer_name(args.repo, layers)
    if any(layer.get("name") == name for layer in layers):
        if explicit_name:
            raise LayerError(f"Layer `{name}` already exists")
        name = unique_layer_name(name, layers)
    layer = {"name": name, "repo": args.repo, "enabled": True}
    if args.revision:
        layer["revision"] = args.revision

    index = insertion_index(layers, before=args.before, after=args.after, top=args.top)
    layers.insert(index, layer)
    save_manifest(root, manifest)

    if not args.no_sync:
        sync_layer(root, layer, clone_only=True)
    if not args.no_compose:
        result = compose(root, manifest, sync=False)
        print_compose_result(result)
        print(f"Added layer {index + 1}: {name}")
        print(f"Source: {args.repo}")
        return 1 if result["conflicts"] else 0
    print(f"Added layer {index + 1}: {name}")
    print(f"Source: {args.repo}")
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
