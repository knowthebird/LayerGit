from __future__ import annotations

import filecmp
import json
from pathlib import Path
from typing import Any

from .composer import file_providers
from .gitops import current_branch, current_commit, is_git_repo, layer_cache_path, porcelain_status, run_git
from .manifest import conflicts_path, output_path, ownership_path, source_path_for_buildtree


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def workspace_status(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    raw_ownership = load_json(ownership_path(root), {})
    ownership = current_ownership(raw_ownership, manifest)
    buildtree = buildtree_state(root, manifest, ownership, raw_ownership)
    conflict_data = current_conflict_data(
        load_json(conflicts_path(root), {"conflicts": [], "warnings": []}),
        manifest,
    )
    layers = []
    enabled_indexes = [
        index
        for index, layer in enumerate(manifest.get("layers", []), start=1)
        if layer.get("enabled", True)
    ]
    top_enabled_index = enabled_indexes[-1] if enabled_indexes else None
    for index, layer in enumerate(manifest.get("layers", []), start=1):
        cache = layer_cache_path(root, layer["name"])
        status = porcelain_status(cache)
        layers.append(
            {
                "index": index,
                "name": layer["name"],
                "kind": layer.get("kind", "git"),
                "repo": layer.get("repo"),
                "mount": layer.get("mount", "/"),
                "enabled": layer.get("enabled", True),
                "position": layer_position(index, enabled_indexes),
                "status": status,
                "dirty": status == "modified",
                "branch": current_branch(cache),
                "commit": current_commit(cache),
                "revision": current_commit(cache),
                "top": index == top_enabled_index,
            }
        )

    return {
        "workspace": str(root),
        "output": manifest.get("workspace", {}).get("output", "./buildtree"),
        "write_layer": manifest.get("workspace", {}).get("write_layer"),
        "layers": layers,
        "composed_tree": {
            "output": manifest.get("workspace", {}).get("output", "./buildtree"),
            "visible_files": sum(
                1 for item in ownership.values() if item.get("visible") is not None
            ),
            "masked_files": sum(len(item.get("masked", [])) for item in ownership.values()),
            "untracked_files": len(buildtree["untracked"]),
            "stale_owned_files": len(buildtree["stale_owned"]),
            "conflicts": len(conflict_data.get("conflicts", [])),
            "warnings": len(conflict_data.get("warnings", [])),
        },
        "buildtree": buildtree,
        "conflicts": conflict_data.get("conflicts", []),
        "warnings": conflict_data.get("warnings", []),
        "modified_files": modified_output_files(root, manifest, ownership),
    }


def doctor_report(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    status = workspace_status(root, manifest)
    checks: list[dict[str, Any]] = []

    def add(check_id: str, level: str, message: str, **extra: Any) -> None:
        item = {"id": check_id, "level": level, "message": message}
        item.update({key: value for key, value in extra.items() if value is not None})
        checks.append(item)

    add("workspace.initialized", "ok", "workspace initialized")
    add("workspace.manifest", "ok", "manifest: layer.yaml", path="layer.yaml")
    output = manifest.get("workspace", {}).get("output", "./buildtree")
    output_dir = output_path(root, manifest)
    add(
        "workspace.output",
        "ok" if output_dir.exists() and output_dir.is_dir() else "warning",
        f"output: {output}",
        path=output,
    )

    layers = manifest.get("layers", [])
    enabled_layer_names = {layer.get("name") for layer in layers if layer.get("enabled", True)}
    write_layer = manifest.get("workspace", {}).get("write_layer")
    if write_layer and write_layer in enabled_layer_names:
        add("workspace.write_layer", "ok", f"write layer: {write_layer}", layer=write_layer)
    elif write_layer:
        add("workspace.write_layer", "error", f"write layer {write_layer} is missing or disabled", layer=write_layer)
    else:
        add("workspace.write_layer", "warning", "no write layer configured")

    tree = status["composed_tree"]
    stale_count = tree.get("stale_owned_files", 0)
    add(
        "generated.ownership",
        "warning" if stale_count else "ok",
        "ownership metadata current" if not stale_count else f"{stale_count} stale-owned files",
        count=stale_count,
    )
    unowned_count = tree.get("untracked_files", 0)
    add(
        "generated.unowned",
        "warning" if unowned_count else "ok",
        f"{unowned_count} unowned files in buildtree",
        count=unowned_count,
    )
    hidden_paths = [
        rel_path
        for rel_path, entry in current_ownership(load_json(ownership_path(root), {}), manifest).items()
        if entry.get("hidden")
    ]
    add(
        "generated.hidden",
        "warning" if hidden_paths else "ok",
        f"{len(hidden_paths)} hidden-by-selection paths",
        count=len(hidden_paths),
        paths=hidden_paths[:20],
    )

    add("layers.configured", "ok" if layers else "warning", f"{len(layers)} layers configured", count=len(layers))
    for layer in layers:
        layer_name = layer.get("name")
        cache = layer_cache_path(root, str(layer_name))
        if not cache.exists():
            add("layer.cache_missing", "error", f"{layer_name} cache repo is missing", layer=layer_name, path=cache.as_posix())
            continue
        try:
            repo_ok = is_git_repo(cache)
        except Exception as exc:
            add("layer.cache_git", "error", f"{layer_name} cache repo cannot be checked: {exc}", layer=layer_name)
            continue
        if not repo_ok:
            add("layer.cache_git", "error", f"{layer_name} cache is not a Git repo", layer=layer_name, path=cache.as_posix())
            continue
        add("layer.cache", "ok", f"{layer_name} cache repo exists", layer=layer_name, path=cache.as_posix())
        porcelain = git_porcelain(cache)
        staged = [line for line in porcelain if is_staged_status(line)]
        untracked = [line for line in porcelain if line.startswith("??")]
        unstaged = [line for line in porcelain if is_unstaged_status(line)]
        if staged:
            add("layer.staged", "warning", f"{layer_name} has staged changes", layer=layer_name, count=len(staged))
        if untracked:
            add("layer.untracked", "warning", f"{layer_name} has untracked files", layer=layer_name, count=len(untracked))
        if staged or unstaged or untracked:
            add("layer.dirty", "warning", f"{layer_name} has uncommitted changes", layer=layer_name, count=len(porcelain))
        else:
            add("layer.clean", "ok", f"{layer_name} has no uncommitted changes", layer=layer_name)
        if current_commit(cache) is None:
            add("layer.no_commits", "warning", f"{layer_name} has no commits", layer=layer_name)
        if layer.get("kind") == "local":
            add("sharing.local_only", "warning", f"{layer_name} is local-only and cannot be reproduced from remotes", layer=layer_name)
        unpushed = unpushed_commit_count(cache)
        if unpushed:
            add("sharing.unpushed", "warning", f"{layer_name} has {unpushed} unpushed commits", layer=layer_name, count=unpushed)

    overlaps = overlap_report(root, manifest)
    overlap_count = len(overlaps.get("overlaps", []))
    add(
        "overlaps.summary",
        "warning" if overlap_count else "ok",
        f"{overlap_count} overlapping paths",
        count=overlap_count,
    )

    summary = {
        "ok": sum(1 for item in checks if item["level"] == "ok"),
        "warnings": sum(1 for item in checks if item["level"] == "warning"),
        "errors": sum(1 for item in checks if item["level"] == "error"),
    }
    overall = "error" if summary["errors"] else "warning" if summary["warnings"] else "ok"
    return {
        "status": overall,
        "workspace": {
            "root": str(root),
            "output": output,
            "manifest": "layer.yaml",
        },
        "checks": checks,
        "summary": summary,
    }


def git_porcelain(path: Path) -> list[str]:
    result = run_git(["status", "--porcelain"], path, check=False)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def is_staged_status(line: str) -> bool:
    return len(line) >= 2 and line[:2] != "??" and line[0] != " "


def is_unstaged_status(line: str) -> bool:
    return len(line) >= 2 and line[:2] != "??" and line[1] != " "


def unpushed_commit_count(path: Path) -> int:
    result = run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], path, check=False)
    if result.returncode != 0:
        return 0
    count = run_git(["rev-list", "--count", "@{upstream}..HEAD"], path, check=False)
    if count.returncode != 0:
        return 0
    try:
        return int(count.stdout.strip() or "0")
    except ValueError:
        return 0


def layer_position(index: int, enabled_indexes: list[int]) -> str | None:
    if index not in enabled_indexes:
        return None
    if len(enabled_indexes) == 1:
        return "top"
    if index == enabled_indexes[0]:
        return "bottom"
    if index == enabled_indexes[-1]:
        return "top"
    return None


def layer_list(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    status = workspace_status(root, manifest)
    return {
        "workspace": status["workspace"],
        "output": status["output"],
        "layers": status["layers"],
        "conflicts": status["conflicts"],
        "warnings": status["warnings"],
    }


def composed_tree(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    raw_ownership = load_json(ownership_path(root), {})
    ownership = current_ownership(raw_ownership, manifest)
    buildtree = buildtree_state(root, manifest, ownership, raw_ownership)
    layer_indexes = {
        layer["name"]: index
        for index, layer in enumerate(manifest.get("layers", []), start=1)
    }
    files = []
    for rel_path, entry in sorted(ownership.items()):
        visible = entry.get("visible") or {}
        visible_layer = visible.get("layer")
        files.append(
            {
                "path": rel_path,
                "type": "file",
                "owned": True,
                "ownership": "composed",
                "visibleLayer": visible_layer,
                "visibleLayerIndex": layer_indexes.get(visible_layer),
                "sourcePath": visible.get("source_path"),
                "source_path": visible.get("source_path"),
                "mount": visible.get("mount"),
                "selectedLayer": entry.get("selected_layer"),
                "selectedMount": entry.get("selected_mount"),
                "hidden": bool(entry.get("hidden")),
                "reason": entry.get("reason"),
                "maskedByThisFile": [
                    item.get("layer")
                    for item in entry.get("masked", [])
                    if item.get("layer")
                ],
                "maskedProviders": entry.get("masked", []),
            }
        )
    for rel_path in buildtree["stale_owned"]:
        files.append(
            {
                "path": rel_path,
                "type": "file",
                "owned": False,
                "ownership": "stale",
                "visibleLayer": None,
                "visibleLayerIndex": None,
                "sourcePath": None,
                "source_path": None,
                "mount": None,
                "selectedLayer": None,
                "selectedMount": None,
                "hidden": False,
                "reason": None,
                "maskedByThisFile": [],
                "maskedProviders": [],
            }
        )
    for rel_path in buildtree["untracked"]:
        files.append(
            {
                "path": rel_path,
                "type": "file",
                "owned": False,
                "ownership": "untracked",
                "visibleLayer": None,
                "visibleLayerIndex": None,
                "sourcePath": None,
                "source_path": None,
                "mount": None,
                "selectedLayer": None,
                "selectedMount": None,
                "hidden": False,
                "reason": None,
                "maskedByThisFile": [],
                "maskedProviders": [],
            }
        )
    files.sort(key=lambda item: item["path"])
    return {
        "workspace": str(root),
        "output": manifest.get("workspace", {}).get("output", "./buildtree"),
        "files": files,
    }


def overlap_report(root: Path, manifest: dict[str, Any], rel_path: str | None = None) -> dict[str, Any]:
    raw_ownership = load_json(ownership_path(root), {})
    ownership = current_ownership(raw_ownership, manifest)
    status = workspace_status(root, manifest)
    overlaps = []
    for path, entry in sorted(ownership.items()):
        if rel_path is not None and path != rel_path:
            continue
        visible = entry.get("visible")
        masked = entry.get("masked", [])
        if not masked:
            continue
        if not visible and not entry.get("hidden"):
            continue
        overlaps.append(
            {
                "path": path,
                "visible": visible,
                "selected_layer": entry.get("selected_layer"),
                "masked": masked,
                "reason": overlap_reason(entry.get("reason")),
            }
        )
    return {
        "workspace": str(root),
        "output": manifest.get("workspace", {}).get("output", "./buildtree"),
        "stale": status["composed_tree"].get("stale_owned_files", 0) > 0,
        "overlaps": overlaps,
    }


def overlap_reason(reason: str | None) -> str:
    if reason == "default top-layer-wins precedence":
        return "top-layer-wins"
    if reason == "file-specific layer selection in layer.yaml":
        return "selected by layer use"
    return reason or "top-layer-wins"


def format_overlaps(data: dict[str, Any], rel_path: str | None = None) -> str:
    lines: list[str] = []
    if data.get("stale"):
        lines.extend(["WARNING: ownership metadata may be stale; run `layer compose`.", ""])
    overlaps = data.get("overlaps", [])
    if not overlaps:
        if rel_path:
            return "\n".join(lines + [f"No overlapping paths for {rel_path}."])
        return "\n".join(lines + ["No overlapping paths."])
    lines.append("Overlapping paths:")
    for entry in overlaps:
        visible = entry.get("visible") or {}
        masked = entry.get("masked", [])
        masked_layers = ", ".join(item.get("layer", "-") for item in masked)
        visible_layer = visible.get("layer") if visible else "<hidden>"
        lines.extend(
            [
                "",
                entry["path"],
                f"  visible: {visible_layer}",
                *([f"  assigned layer: {entry.get('selected_layer')}"] if entry.get("selected_layer") else []),
                f"  masked:  {masked_layers or '-'}",
                f"  reason:  {entry.get('reason') or '-'}",
            ]
        )
    return "\n".join(lines)


def modified_output_files(
    root: Path,
    manifest: dict[str, Any],
    ownership: dict[str, Any],
) -> list[dict[str, str]]:
    output = output_path(root, manifest)
    modified: list[dict[str, str]] = []
    for rel_path, entry in ownership.items():
        target = output / rel_path
        visible = entry.get("visible")
        if not visible:
            continue
        source = layer_cache_path(root, visible.get("layer", "")) / visible.get("source_path", "")
        if target.exists() and source.exists() and not filecmp.cmp(target, source, shallow=False):
            modified.append({"path": rel_path, "layer": visible.get("layer", "")})
    return modified


def current_ownership(
    ownership: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    active_layers = active_layer_names(manifest)
    current: dict[str, Any] = {}
    for rel_path, entry in ownership.items():
        normalized = current_ownership_entry(entry, active_layers)
        if normalized is not None:
            current[rel_path] = normalized
    return current


def current_ownership_entry(
    entry: dict[str, Any],
    active_layers: set[str],
) -> dict[str, Any] | None:
    if not active_layers:
        return None
    visible = entry.get("visible")
    masked = [
        item
        for item in entry.get("masked", [])
        if item.get("layer") in active_layers
    ]
    if visible:
        if visible.get("layer") not in active_layers:
            return None
        return {**entry, "masked": masked}
    selected_layer = entry.get("selected_layer")
    if entry.get("hidden") and selected_layer in active_layers and masked:
        return {**entry, "masked": masked}
    return None


def current_conflict_data(conflict_data: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    active_layers = active_layer_names(manifest)
    return {
        "conflicts": current_findings(conflict_data.get("conflicts", []), active_layers),
        "warnings": current_findings(conflict_data.get("warnings", []), active_layers),
    }


def current_findings(findings: list[dict[str, Any]], active_layers: set[str]) -> list[dict[str, Any]]:
    if not active_layers:
        return []
    current = []
    for finding in findings:
        providers = finding.get("providers", [])
        if providers and all(provider.get("layer") in active_layers for provider in providers):
            current.append(finding)
    return current


def active_layer_names(manifest: dict[str, Any]) -> set[str]:
    return {
        layer["name"]
        for layer in manifest.get("layers", [])
        if layer.get("enabled", True)
    }


def buildtree_state(
    root: Path,
    manifest: dict[str, Any],
    ownership: dict[str, Any],
    raw_ownership: dict[str, Any],
) -> dict[str, list[str]]:
    output_files = set(iter_output_files(output_path(root, manifest)))
    current_visible = visible_output_paths(ownership)
    previously_visible = visible_output_paths(raw_ownership)
    previously_logical = logical_output_paths(raw_ownership)
    stale_owned = sorted((previously_visible - current_visible) & output_files)
    untracked = sorted(output_files - previously_logical)
    return {"untracked": untracked, "stale_owned": stale_owned}


def iter_output_files(output: Path) -> list[str]:
    if not output.exists():
        return []
    return [
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    ]


def visible_output_paths(ownership: dict[str, Any]) -> set[str]:
    return {
        rel_path
        for rel_path, entry in ownership.items()
        if entry.get("visible") is not None
    }


def logical_output_paths(ownership: dict[str, Any]) -> set[str]:
    return {
        rel_path
        for rel_path, entry in ownership.items()
        if entry.get("visible") is not None or entry.get("hidden")
    }


def format_status_short(status: dict[str, Any]) -> str:
    lines = ["Layers:"]
    if not status["layers"]:
        lines.append("  <none>")
    for layer in status["layers"]:
        branch = layer.get("branch") or "-"
        commit = layer.get("commit") or "-"
        suffix = "  top" if layer.get("top") else ""
        enabled = "enabled" if layer.get("enabled") else "disabled"
        kind = layer.get("kind", "git")
        write = "  write" if status.get("write_layer") == layer.get("name") else ""
        mount = layer.get("mount", "/")
        lines.append(
            f"  {layer['index']} {layer['name']:<16} {kind:<5} {mount:<12} {enabled:<8} {layer['status']:<9} {branch} @ {commit}{suffix}{write}"
        )
    if status.get("write_layer"):
        lines.extend(["", f"Write layer: {status['write_layer']}"])

    tree = status["composed_tree"]
    lines.extend(
        [
            "",
            "Buildtree:",
            f"  output: {tree['output']}",
            f"  visible composed files: {tree['visible_files']}",
            f"  masked files: {tree['masked_files']}",
            f"  untracked buildtree files: {tree.get('untracked_files', 0)}",
            f"  stale owned files: {tree.get('stale_owned_files', 0)}",
            f"  conflicts: {tree['conflicts']}",
            f"  warnings: {tree.get('warnings', 0)}",
        ]
    )

    if status["conflicts"]:
        lines.extend(["", "Conflicts:"])
        for conflict in status["conflicts"]:
            providers = " and ".join(provider["layer"] for provider in conflict["providers"])
            lines.append(f"  {conflict['path']} exists in {providers}")

    if status.get("warnings"):
        lines.extend(["", "Warnings:"])
        for warning in status["warnings"]:
            providers = " and ".join(provider["layer"] for provider in warning["providers"])
            lines.append(f"  {warning['path']} appears in {providers}")

    if status["modified_files"]:
        lines.extend(["", "Modified files:"])
        for item in status["modified_files"]:
            lines.append(f"  {item['path']} -> {item['layer']}")

    buildtree = status.get("buildtree", {})
    if buildtree.get("untracked"):
        lines.extend(["", "Untracked buildtree files:"])
        for rel_path in buildtree["untracked"]:
            lines.append(f"  {rel_path}")
        if not status.get("write_layer"):
            lines.extend(
                [
                    "",
                    "Create or select a local layer:",
                    "  layer add --local local-edits",
                    "  layer write local-edits",
                ]
            )
    if buildtree.get("stale_owned"):
        lines.extend(["", "Stale owned files:"])
        for rel_path in buildtree["stale_owned"]:
            lines.append(f"  {rel_path}")

    return "\n".join(lines)


def format_doctor_report(data: dict[str, Any]) -> str:
    lines = ["LayerGit doctor", ""]
    sections = [
        ("Workspace", "workspace."),
        ("Generated state", "generated."),
        ("Layers", "layer."),
        ("Overlaps", "overlaps."),
        ("Sharing", "sharing."),
    ]
    checks = data.get("checks", [])
    used_ids: set[str] = set()
    for title, prefix in sections:
        section_checks = [item for item in checks if str(item.get("id", "")).startswith(prefix)]
        if not section_checks:
            continue
        lines.append(title)
        for item in section_checks:
            used_ids.add(str(item.get("id")))
            lines.append(f"  {doctor_level_label(item.get('level')):<7} {item.get('message')}")
            if item.get("id") == "overlaps.summary" and item.get("count", 0):
                lines.append("          run `layer overlaps` to inspect visible/masked providers")
        lines.append("")
    other_checks = [item for item in checks if str(item.get("id")) not in used_ids]
    if other_checks:
        lines.append("Other")
        for item in other_checks:
            lines.append(f"  {doctor_level_label(item.get('level')):<7} {item.get('message')}")
        lines.append("")
    result = data.get("status", "ok")
    if result == "error":
        lines.append("Result: errors found")
    elif result == "warning":
        lines.append("Result: warnings found")
    else:
        lines.append("Result: ok")
    return "\n".join(lines).rstrip()


def doctor_level_label(level: Any) -> str:
    if level == "warning":
        return "WARN"
    if level == "error":
        return "ERROR"
    return "OK"


def format_status(status: dict[str, Any]) -> str:
    tree = status["composed_tree"]
    lines = [
        "LayerGit status",
        "",
        f"Workspace: {status.get('workspace', '-')}",
        f"Output:    {tree.get('output') or status.get('output') or './buildtree'}",
        "Order:     top -> bottom; higher layers win path conflicts",
        f"Write:     {status.get('write_layer') or '<none>'}",
        "",
        "Layers:",
    ]
    if not status["layers"]:
        lines.append("  <none>")
    else:
        layers = list(reversed(status["layers"]))
        headers = ["ORDER", "NAME", "TYPE", "MOUNT", "STATE", "GIT", "BRANCH", "COMMIT", "FLAGS"]
        enabled_count = sum(1 for layer in status["layers"] if layer.get("enabled"))
        rows = [status_layer_row(layer, status.get("write_layer"), enabled_count) for layer in layers]
        widths = [
            max(len(headers[index]), *(len(row[index]) for row in rows))
            for index in range(len(headers))
        ]
        lines.append("  " + format_status_row(headers, widths))
        for row in rows:
            lines.append("  " + format_status_row(row, widths))

    enabled = sum(1 for layer in status["layers"] if layer.get("enabled"))
    disabled = len(status["layers"]) - enabled
    dirty = sum(1 for layer in status["layers"] if layer.get("dirty") or layer.get("status") not in ("clean", "missing"))
    lines.extend(
        [
            "",
            "Summary:",
            f"  {enabled} layers enabled",
            f"  {disabled} disabled",
            f"  {dirty} dirty",
            f"  {tree['conflicts']} conflicts",
            f"  {tree.get('warnings', 0)} warnings",
            f"  {tree['visible_files']} visible composed files",
            f"  {tree['masked_files']} masked files",
            f"  {tree.get('untracked_files', 0)} untracked buildtree files",
            f"  {tree.get('stale_owned_files', 0)} stale owned files",
        ]
    )

    if status["conflicts"]:
        lines.extend(["", "Conflicts:"])
        for conflict in status["conflicts"]:
            providers = " and ".join(provider["layer"] for provider in conflict["providers"])
            lines.append(f"  {conflict['path']} exists in {providers}")

    if status.get("warnings"):
        lines.extend(["", "Warnings:"])
        for warning in status["warnings"]:
            providers = " and ".join(provider["layer"] for provider in warning["providers"])
            lines.append(f"  {warning['path']} appears in {providers}")

    if status["modified_files"]:
        lines.extend(["", "Modified files:"])
        for item in status["modified_files"]:
            lines.append(f"  {item['path']} -> {item['layer']}")

    buildtree = status.get("buildtree", {})
    if buildtree.get("untracked"):
        lines.extend(["", "Untracked buildtree files:"])
        for rel_path in buildtree["untracked"]:
            lines.append(f"  {rel_path}")
        if not status.get("write_layer"):
            lines.extend(
                [
                    "",
                    "Create or select a local layer:",
                    "  layer add --local local-edits",
                    "  layer write local-edits",
                ]
            )
    if buildtree.get("stale_owned"):
        lines.extend(["", "Stale owned files:"])
        for rel_path in buildtree["stale_owned"]:
            lines.append(f"  {rel_path}")

    return "\n".join(lines)


def status_layer_row(layer: dict[str, Any], write_layer: str | None, enabled_count: int) -> list[str]:
    enabled = "enabled" if layer.get("enabled") else "disabled"
    branch = layer.get("branch") or "-"
    commit = layer.get("commit") or "no commits"
    flags = []
    if write_layer == layer.get("name"):
        flags.append("write-layer")
    if layer.get("top"):
        flags.append("top")
    if layer.get("position") == "bottom" or (enabled_count == 1 and layer.get("enabled") and layer.get("top")):
        flags.append("bottom")
    return [
        str(layer["index"]),
        layer["name"],
        layer.get("kind", "git"),
        layer.get("mount", "/"),
        enabled,
        status_git_label(layer.get("status")),
        branch,
        commit,
        ", ".join(flags) or "-",
    ]


def status_git_label(status: str | None) -> str:
    if status == "modified":
        return "dirty"
    return status or "-"


def format_status_row(row: list[str], widths: list[int]) -> str:
    return "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip()


def explain_file(root: Path, rel_path: str, manifest: dict[str, Any] | None = None) -> dict[str, Any] | None:
    raw_ownership = load_json(ownership_path(root), {})
    ownership = current_ownership(raw_ownership, manifest) if manifest is not None else raw_ownership
    entry = ownership.get(rel_path)
    if entry is not None or manifest is None:
        return entry
    output = output_path(root, manifest)
    output_file = output / rel_path
    if output_file.exists() and output_file.is_file():
        if rel_path in visible_output_paths(raw_ownership):
            return {
                "visible": None,
                "masked": [],
                "unowned": True,
                "stale_owned": True,
                "reason": "previously owned by LayerGit but no longer valid for current layer.yaml",
            }
        return {
            "visible": None,
            "masked": [],
            "unowned": True,
            "untracked": True,
            "reason": "file exists in buildtree but is not owned by any layer",
        }
    all_providers = file_providers(root, manifest, rel_path, include_disabled=True)
    enabled_providers = set(file_providers(root, manifest, rel_path))
    disabled_providers = [
        provider for provider in all_providers if provider not in enabled_providers
    ]
    if not disabled_providers:
        return None
    return {
        "visible": None,
        "masked": [],
        "disabled_providers": [
            disabled_provider_entry(manifest, rel_path, provider)
            for provider in disabled_providers
        ],
        "reason": "disabled layer providers are not included in composition",
    }


def disabled_provider_entry(manifest: dict[str, Any], rel_path: str, layer_name: str) -> dict[str, Any]:
    layer = next(
        (item for item in manifest.get("layers", []) if item.get("name") == layer_name),
        {},
    )
    mount = layer.get("mount", "/")
    source_path = source_path_for_buildtree(rel_path, mount) or rel_path
    return {"layer": layer_name, "source_path": source_path, "mount": mount, "path": rel_path}


def explain_json(root: Path, rel_path: str, manifest: dict[str, Any]) -> dict[str, Any] | None:
    entry = explain_file(root, rel_path, manifest)
    if entry is None:
        return None
    layer_indexes = {
        layer["name"]: index
        for index, layer in enumerate(manifest.get("layers", []), start=1)
    }
    result = {"path": rel_path, **entry}
    visible = result.get("visible")
    if visible:
        add_extension_fields(visible, layer_indexes)
    for item in result.get("masked", []):
        add_extension_fields(item, layer_indexes)
    for item in result.get("disabled_providers", []):
        add_extension_fields(item, layer_indexes)
    return result


def add_extension_fields(item: dict[str, Any], layer_indexes: dict[str, int]) -> None:
    layer = item.get("layer")
    item.setdefault("layerIndex", layer_indexes.get(layer))
    if "source_path" in item:
        item.setdefault("sourcePath", item["source_path"])
    if "commit" in item:
        item.setdefault("revision", item["commit"])


def format_explain(rel_path: str, entry: dict[str, Any] | None) -> str:
    if entry is None:
        return f"No ownership record for {rel_path}"
    if entry.get("unowned"):
        lines = [
            rel_path,
            "",
            "Owned by LayerGit:",
            "  no",
        ]
        if entry.get("stale_owned"):
            lines.extend(["", "State:", "  stale owned file"])
        elif entry.get("untracked"):
            lines.extend(["", "State:", "  untracked buildtree file"])
        if entry.get("reason"):
            lines.extend(["", "Reason:", f"  {entry['reason']}"])
        return "\n".join(lines)
    if entry.get("visible") is None:
        selected_layer = entry.get("selected_layer")
        if entry.get("hidden"):
            lines = [
                rel_path,
                "",
                "Hidden by selection",
                f"  assigned layer: {selected_layer or '-'}",
            ]
            if entry.get("reason"):
                lines.append(f"  reason: {entry['reason']}")
            lines.extend(["", "Masked providers:"])
            for item in entry.get("masked", []):
                lines.append(f"  {item.get('layer')}/{item.get('source_path')} (mount {item.get('mount', '/')})")
            return "\n".join(lines)
        lines = [
            rel_path,
            "",
            "Visible file:",
            "  none",
        ]
        if selected_layer:
            lines.extend(["", "Selected layer:", f"  {selected_layer}"])
        if entry.get("disabled_providers"):
            lines.extend(["", "Disabled providers:"])
            for item in entry.get("disabled_providers", []):
                lines.append(f"  {item.get('layer')}/{item.get('source_path')} (mount {item.get('mount', '/')})")
        else:
            lines.extend(["", "Masked providers:"])
            for item in entry.get("masked", []):
                lines.append(f"  {item.get('layer')}/{item.get('source_path')} (mount {item.get('mount', '/')})")
        if entry.get("reason"):
            lines.extend(["", "Reason:", f"  {entry['reason']}"])
        return "\n".join(lines)
    visible = entry["visible"]
    lines = [
        rel_path,
        "",
        "Visible file:",
        f"  layer: {visible.get('layer')}",
        f"  repo: {visible.get('repo')}",
        f"  commit: {visible.get('commit')}",
        f"  source path: {visible.get('source_path')}",
        f"  mount: {visible.get('mount', '/')}",
    ]
    masked = entry.get("masked", [])
    if masked:
        lines.extend(["", "Masked lower-layer files:"])
        for item in masked:
            lines.append(f"  {item.get('layer')}/{item.get('source_path')} (mount {item.get('mount', '/')})")
    if entry.get("reason"):
        lines.extend(["", "Reason:", f"  {entry['reason']}"])
    return "\n".join(lines)
