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
    ownership = load_json(ownership_path(root), {})
    conflict_data = load_json(conflicts_path(root), {"conflicts": []})
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
        "layers": layers,
        "composed_tree": {
            "output": manifest.get("workspace", {}).get("output", "./buildtree"),
            "visible_files": sum(
                1 for item in ownership.values() if item.get("visible") is not None
            ),
            "masked_files": sum(len(item.get("masked", [])) for item in ownership.values()),
            "conflicts": len(conflict_data.get("conflicts", [])),
            "warnings": len(conflict_data.get("warnings", [])),
        },
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
    ownership = load_json(ownership_path(root), {})
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
    return {
        "workspace": str(root),
        "output": manifest.get("workspace", {}).get("output", "./buildtree"),
        "files": files,
    }


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


def format_status(status: dict[str, Any]) -> str:
    lines = ["Layers:"]
    if not status["layers"]:
        lines.append("  <none>")
    for layer in status["layers"]:
        branch = layer.get("branch") or "-"
        commit = layer.get("commit") or "-"
        suffix = "  top" if layer.get("top") else ""
        enabled = "enabled" if layer.get("enabled") else "disabled"
        lines.append(
            f"  {layer['index']} {layer['name']:<16} {enabled:<8} {layer['status']:<9} {branch} @ {commit}{suffix}"
        )

    tree = status["composed_tree"]
    lines.extend(
        [
            "",
            "Composed tree:",
            f"  output: {tree['output']}",
            f"  visible files: {tree['visible_files']}",
            f"  masked files: {tree['masked_files']}",
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

    return "\n".join(lines)


def explain_file(root: Path, rel_path: str, manifest: dict[str, Any] | None = None) -> dict[str, Any] | None:
    ownership = load_json(ownership_path(root), {})
    entry = ownership.get(rel_path)
    if entry is not None or manifest is None:
        return entry
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
