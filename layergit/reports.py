from __future__ import annotations

import filecmp
import json
from pathlib import Path
from typing import Any

from .composer import file_providers
from .gitops import current_branch, current_commit, layer_cache_path, porcelain_status
from .manifest import conflicts_path, output_path, ownership_path


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
                "selectedLayer": entry.get("selected_layer"),
                "hidden": bool(entry.get("hidden")),
                "maskedByThisFile": [
                    item.get("layer")
                    for item in entry.get("masked", [])
                    if item.get("layer")
                ],
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
                "selectedLayer": None,
                "hidden": False,
                "maskedByThisFile": [],
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
                "selectedLayer": None,
                "hidden": False,
                "maskedByThisFile": [],
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
        if not visible or not masked:
            continue
        overlaps.append(
            {
                "path": path,
                "visible": visible,
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
        lines.extend(
            [
                "",
                entry["path"],
                f"  visible: {visible.get('layer', '-')}",
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
    previously_owned = visible_output_paths(raw_ownership)
    stale_owned = sorted((previously_owned - current_visible) & output_files)
    untracked = sorted(output_files - previously_owned)
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
        lines.append(
            f"  {layer['index']} {layer['name']:<16} {kind:<5} {enabled:<8} {layer['status']:<9} {branch} @ {commit}{suffix}{write}"
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
        headers = ["ORDER", "NAME", "TYPE", "STATE", "GIT", "BRANCH", "COMMIT", "FLAGS"]
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
            {"layer": provider, "source_path": rel_path}
            for provider in disabled_providers
        ],
        "reason": "disabled layer providers are not included in composition",
    }


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
        lines = [
            rel_path,
            "",
            "Visible file:",
            "  none",
        ]
        if selected_layer:
            lines.extend(["", "Selected layer:", f"  {selected_layer}"])
        if entry.get("hidden"):
            lines.extend(["", "Hidden providers:"])
            for item in entry.get("masked", []):
                lines.append(f"  {item.get('layer')}/{item.get('source_path')}")
        else:
            lines.extend(["", "Disabled providers:"])
            for item in entry.get("disabled_providers", []):
                lines.append(f"  {item.get('layer')}/{item.get('source_path')}")
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
    ]
    masked = entry.get("masked", [])
    if masked:
        lines.extend(["", "Masked lower-layer files:"])
        for item in masked:
            lines.append(f"  {item.get('layer')}/{item.get('source_path')}")
    if entry.get("reason"):
        lines.extend(["", "Reason:", f"  {entry['reason']}"])
    return "\n".join(lines)
