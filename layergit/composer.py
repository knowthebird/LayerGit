from __future__ import annotations

import fnmatch
import filecmp
import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .errors import LayerError
from .gitops import current_commit, layer_cache_path, porcelain_status, sync_layer, tracked_files
from .manifest import (
    buildtree_path_for_source,
    conflicts_path,
    lockfile_path,
    output_path,
    ownership_path,
    source_path_for_buildtree,
)


@dataclass(frozen=True)
class Provider:
    layer_index: int
    layer_name: str
    repo: str | None
    commit: str | None
    source_root: Path
    source_path: str
    abs_path: Path
    overrides: tuple[str, ...]
    mount: str = "/"
    output_path: str | None = None


def compose(
    root: Path,
    manifest: dict[str, Any],
    *,
    sync: bool = True,
    clean: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    layers = manifest.get("layers", [])
    enabled_layers = [
        (layer_index, layer)
        for layer_index, layer in enumerate(layers)
        if layer.get("enabled", True)
    ]
    if sync:
        for _, layer in enabled_layers:
            sync_layer(root, layer, clone_only=True)

    providers_by_path: dict[str, list[Provider]] = {}
    for layer_index, layer in enabled_layers:
        source_root = layer_cache_path(root, layer["name"])
        mount = layer.get("mount", "/")
        if not source_root.exists():
            raise LayerError(f"Cache for layer `{layer['name']}` is missing: {source_root}")
        for rel_path, abs_path in iter_layer_files(source_root, layer):
            buildtree_rel_path = buildtree_path_for_source(rel_path, mount)
            provider = Provider(
                layer_index=layer_index,
                layer_name=layer["name"],
                repo=layer.get("repo"),
                commit=current_commit(source_root),
                source_root=source_root,
                source_path=rel_path,
                abs_path=abs_path,
                overrides=tuple(layer.get("overrides") or ()),
                mount=mount,
                output_path=buildtree_rel_path,
            )
            providers_by_path.setdefault(buildtree_rel_path, []).append(provider)
    add_adopted_cache_file_providers(root, manifest, enabled_layers, providers_by_path)

    ownership: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    visible: dict[str, Provider | None] = {}
    same_path_policy = manifest.get("composition", {}).get("same_path_policy", "top_wins")
    layers_by_name = {layer.get("name"): layer for layer in layers}

    for rel_path in sorted(providers_by_path):
        selected_rule = selected_file_rule(manifest, rel_path)
        selected_layer = selected_file_layer(manifest, rel_path)
        if selected_layer:
            providers = providers_by_path[rel_path]
            selected_provider = (
                None
                if selected_rule.get("hide")
                else next(
                    (provider for provider in providers if provider.layer_name == selected_layer),
                    None,
                )
            )
            if selected_provider is None:
                visible[rel_path] = None
                ownership[rel_path] = hidden_ownership_entry(
                    selected_layer,
                    layers_by_name.get(selected_layer, {}).get("mount", "/"),
                    [provider_entry(provider, visible=False) for provider in reversed(providers)],
                    reason="selected layer does not provide this file",
                )
                continue
            masked = [provider for provider in providers if provider.layer_name != selected_layer]
            visible[rel_path] = selected_provider
            ownership[rel_path] = ownership_entry(
                selected_provider,
                [provider_entry(provider, visible=False) for provider in reversed(masked)],
                reason="file-specific layer selection in layer.yaml",
            )
            continue

        providers, reason, has_file_precedence = order_providers_for_path(
            rel_path,
            providers_by_path[rel_path],
            manifest,
            conflicts,
            warnings,
        )
        if len(providers) == 1:
            winner = providers[0]
            visible[rel_path] = winner
            ownership[rel_path] = ownership_entry(winner, [])
            continue

        winner = providers[-1]
        masked = providers[:-1]
        visible[rel_path] = winner
        ownership[rel_path] = ownership_entry(
            winner,
            [provider_entry(provider, visible=False) for provider in reversed(masked)],
            reason=reason if masked else None,
        )
        if same_path_policy == "error" and not has_file_precedence:
            conflicts.append(
                {
                    "kind": "duplicate_path",
                    "path": rel_path,
                    "providers": [conflict_provider(provider) for provider in providers],
                    "message": "duplicate output path forbidden by same_path_policy",
                }
            )

    policy_conflicts, policy_warnings = duplicate_basename_findings(visible, manifest)
    conflicts.extend(policy_conflicts)
    warnings.extend(policy_warnings)

    previous_ownership = load_existing_ownership(root)
    dirty_owned = dirty_owned_output_files(root, manifest, previous_ownership, visible)
    if dirty_owned and not clean and not dry_run:
        rel_path = dirty_owned[0]["path"]
        layer = dirty_owned[0].get("layer") or "the current owner"
        raise LayerError(
            f"{rel_path} has unapplied buildtree edits.\n\n"
            "Choose an explicit action:\n"
            f"  layer apply {rel_path}\n"
            f"      Apply edits to {layer}.\n\n"
            f"  layer apply {rel_path} --to <layer>\n"
            "      Copy current buildtree content into another layer and assign the path there.\n\n"
            "  layer compose --clean\n"
            "      Discard buildtree edits and regenerate from layers."
        )
    for rel_path, provider, existing_kind in unowned_output_collisions(
        output_path(root, manifest),
        visible,
        previous_ownership,
        clean=clean,
    ):
        visible.pop(rel_path, None)
        ownership.pop(rel_path, None)
        conflicts.append(
            {
                "kind": "unowned_output_path",
                "path": rel_path,
                "providers": [conflict_provider(provider)],
                "message": (
                    f"output {existing_kind} exists but is not owned by LayerGit; "
                    "move it, apply it to a layer, or use compose --clean"
                ),
            }
        )
    if not dry_run:
        write_output_tree(output_path(root, manifest), visible, previous_ownership, clean=clean)
        write_generated_files(root, manifest, ownership, conflicts, warnings)
    return {
        "visible_files": sum(1 for provider in visible.values() if provider is not None),
        "masked_files": sum(len(item.get("masked", [])) for item in ownership.values()),
        "conflicts": conflicts,
        "warnings": warnings,
        "ownership": ownership,
        "dry_run": dry_run,
    }


def dirty_owned_output_files(
    root: Path,
    manifest: dict[str, Any],
    previous_ownership: dict[str, Any],
    planned_visible: dict[str, Provider | None],
) -> list[dict[str, str]]:
    output = output_path(root, manifest)
    dirty: list[dict[str, str]] = []
    for rel_path, entry in sorted(previous_ownership.items()):
        visible = entry.get("visible")
        if not visible:
            continue
        planned_provider = planned_visible.get(rel_path)
        if planned_provider is None:
            continue
        if planned_provider.layer_name != visible.get("layer") or planned_provider.source_path != visible.get("source_path"):
            continue
        if visible.get("commit") and planned_provider.commit != visible.get("commit"):
            continue
        if porcelain_status(planned_provider.source_root) != "clean":
            continue
        target = output / rel_path
        source = planned_provider.abs_path
        if target.exists() and target.is_file() and source.exists() and source.is_file():
            if not filecmp.cmp(target, source, shallow=False):
                dirty.append({"path": rel_path, "layer": visible.get("layer", "")})
    return dirty


def selected_file_layer(manifest: dict[str, Any], rel_path: str) -> str | None:
    rule = selected_file_rule(manifest, rel_path)
    layer = rule.get("layer")
    if not layer:
        return None
    enabled_layers = {
        item["name"]
        for item in manifest.get("layers", [])
        if item.get("enabled", True)
    }
    return layer if layer in enabled_layers else None


def selected_file_rule(manifest: dict[str, Any], rel_path: str) -> dict[str, Any]:
    rule = (manifest.get("file_selection") or {}).get(rel_path) or {}
    return rule if isinstance(rule, dict) else {}


def add_adopted_cache_file_providers(
    root: Path,
    manifest: dict[str, Any],
    enabled_layers: list[tuple[int, dict]],
    providers_by_path: dict[str, list[Provider]],
) -> None:
    enabled_by_name = {
        layer.get("name"): (layer_index, layer)
        for layer_index, layer in enabled_layers
    }
    for buildtree_rel_path, selected_layer in iter_adopted_file_layers(manifest):
        if not selected_layer or selected_layer not in enabled_by_name:
            continue
        layer_index, layer = enabled_by_name[selected_layer]
        mount = layer.get("mount", "/")
        source_rel_path = source_path_for_buildtree(buildtree_rel_path, mount)
        if source_rel_path is None:
            continue
        source_root = layer_cache_path(root, selected_layer)
        source_file = source_root / source_rel_path
        if not source_file.is_file():
            continue
        if not layer_includes_path(layer, source_rel_path):
            continue
        existing = providers_by_path.setdefault(buildtree_rel_path, [])
        if any(provider.layer_name == selected_layer for provider in existing):
            continue
        existing.append(
            Provider(
                layer_index=layer_index,
                layer_name=selected_layer,
                repo=layer.get("repo"),
                commit=current_commit(source_root),
                source_root=source_root,
                source_path=source_rel_path,
                abs_path=source_file,
                overrides=tuple(layer.get("overrides") or ()),
                mount=mount,
                output_path=buildtree_rel_path,
            )
        )


def iter_adopted_file_layers(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    adopted: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for rel_path, rule in (manifest.get("file_selection") or {}).items():
        if not isinstance(rule, dict) or not rule.get("adopted"):
            continue
        layer_name = rule.get("layer")
        if not isinstance(layer_name, str):
            continue
        key = (rel_path, layer_name)
        if key not in seen:
            seen.add(key)
            adopted.append(key)

    return adopted


def iter_layer_files(source_root: Path, layer: dict) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for rel in sorted(tracked_files(source_root)):
        path = source_root / rel
        if not path.is_file():
            continue
        if not layer_includes_path(layer, rel):
            continue
        files.append((rel, path))
    return files


def layer_includes_path(layer: dict, rel_path: str) -> bool:
    include = tuple(layer.get("include") or ("**",))
    exclude = tuple(layer.get("exclude") or ())
    if not any(path_matches(rel_path, pattern) for pattern in include):
        return False
    if any(path_matches(rel_path, pattern) for pattern in exclude):
        return False
    return True


def override_allowed(rel_path: str, provider: Provider, global_overrides: tuple[str, ...]) -> bool:
    patterns = provider.overrides + global_overrides
    return any(path_matches(rel_path, pattern) for pattern in patterns)


def matching_pattern(rel_path: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        if path_matches(rel_path, pattern):
            return pattern
    return "<unknown>"


def order_providers_for_path(
    rel_path: str,
    providers: list[Provider],
    manifest: dict[str, Any],
    conflicts: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> tuple[list[Provider], str, bool]:
    rule = manifest.get("file_precedence", {}).get(rel_path) or {}
    order = rule.get("order") or []
    if not order:
        return providers, "default top-layer-wins precedence", False

    by_name = {provider.layer_name: provider for provider in providers}
    disabled_layers = {
        layer["name"]
        for layer in manifest.get("layers", [])
        if not layer.get("enabled", True)
    }
    disabled_references = [name for name in order if name in disabled_layers]
    if disabled_references:
        warnings.append(
            {
                "kind": "disabled_file_precedence",
                "path": rel_path,
                "providers": [conflict_provider(provider) for provider in providers],
                "requested_layers": disabled_references,
                "message": "file precedence rule references disabled layer",
            }
        )
    active_order = [name for name in order if name not in disabled_layers]
    missing = [name for name in active_order if name not in by_name]
    if missing:
        conflicts.append(
            {
                "kind": "invalid_file_precedence",
                "path": rel_path,
                "providers": [conflict_provider(provider) for provider in providers],
                "requested_layers": missing,
                "message": "file precedence rule references a layer that does not provide the file",
            }
        )
        return providers, "default top-layer-wins precedence", False

    ordered_names = set(active_order)
    unlisted = [provider for provider in providers if provider.layer_name not in ordered_names]
    listed = [by_name[name] for name in active_order]
    return unlisted + listed, "file-specific precedence rule in layer.yaml", True


def file_providers(
    root: Path,
    manifest: dict[str, Any],
    rel_path: str,
    *,
    include_disabled: bool = False,
) -> list[str]:
    providers: list[str] = []
    adopted_layers = adopted_layers_for_path(manifest, rel_path)
    for layer in manifest.get("layers", []):
        if not include_disabled and not layer.get("enabled", True):
            continue
        source_rel_path = source_path_for_buildtree(rel_path, layer.get("mount", "/"))
        if source_rel_path is None:
            continue
        source_root = layer_cache_path(root, layer["name"])
        is_tracked = source_rel_path in tracked_files(source_root)
        is_adopted = layer["name"] in adopted_layers
        if not is_tracked and not is_adopted:
            continue
        source_file = source_root / source_rel_path
        if not source_file.is_file():
            continue
        if not layer_includes_path(layer, source_rel_path):
            continue
        providers.append(layer["name"])
    return providers


def adopted_layers_for_path(manifest: dict[str, Any], rel_path: str) -> set[str]:
    return {
        layer_name
        for adopted_path, layer_name in iter_adopted_file_layers(manifest)
        if adopted_path == rel_path
    }


def duplicate_basename_findings(
    visible: dict[str, Provider | None],
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conflicts_config = manifest.get("conflicts", {})
    patterns = tuple(conflicts_config.get("forbid_duplicate_basenames") or ())
    if not patterns:
        return [], []

    groups: dict[str, list[tuple[str, Provider]]] = {}
    for rel_path, provider in visible.items():
        if provider is None:
            continue
        if any(path_matches(rel_path, pattern) for pattern in patterns):
            groups.setdefault(Path(rel_path).name, []).append((rel_path, provider))

    findings: list[dict[str, Any]] = []
    for basename, matches in sorted(groups.items()):
        if len(matches) < 2:
            continue
        findings.append(
            {
                "kind": "duplicate_basename",
                "path": basename,
                "providers": [
                    {
                        **conflict_provider(provider),
                        "buildtree_path": rel_path,
                    }
                    for rel_path, provider in matches
                ],
                "message": "duplicate source basename forbidden by policy",
            }
        )

    if conflicts_config.get("duplicate_basename_policy", "warn") == "error":
        return findings, []
    return [], findings


def path_matches(rel_path: str, pattern: str) -> bool:
    path = PurePosixPath(rel_path)
    if pattern in ("**", "*"):
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return rel_path == prefix or rel_path.startswith(prefix + "/")
    return path.match(pattern) or fnmatch.fnmatchcase(rel_path, pattern)


def load_existing_ownership(root: Path) -> dict[str, Any]:
    path = ownership_path(root)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def unowned_output_collisions(
    output: Path,
    visible: dict[str, Provider | None],
    previous_ownership: dict[str, Any],
    *,
    clean: bool,
) -> list[tuple[str, Provider, str]]:
    if clean:
        return []
    previous_owned = previously_owned_output_paths(previous_ownership)
    collisions = []
    for rel_path, provider in visible.items():
        if provider is None or rel_path in previous_owned:
            continue
        target = output / rel_path
        if target.exists():
            if target.is_file() and provider.abs_path.is_file() and filecmp.cmp(target, provider.abs_path, shallow=False):
                continue
            collisions.append((rel_path, provider, "directory" if target.is_dir() else "file"))
    return collisions


def write_output_tree(
    output: Path,
    visible: dict[str, Provider | None],
    previous_ownership: dict[str, Any],
    *,
    clean: bool,
) -> None:
    if clean and output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    previous_owned = previously_owned_output_paths(previous_ownership)
    visible_paths = {
        rel_path
        for rel_path, provider in visible.items()
        if provider is not None
    }
    for rel_path in previous_owned - visible_paths:
        target = output / rel_path
        if target.exists() and target.is_file():
            target.unlink()
            remove_empty_parents(target.parent, output)
    for rel_path, provider in visible.items():
        if provider is None:
            continue
        target = output / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(provider.abs_path, target)


def previously_owned_output_paths(ownership: dict[str, Any]) -> set[str]:
    return {
        rel_path
        for rel_path, entry in ownership.items()
        if entry.get("visible") is not None or entry.get("hidden")
    }


def remove_empty_parents(path: Path, stop: Path) -> None:
    while path != stop and path.exists():
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


def write_generated_files(
    root: Path,
    manifest: dict[str, Any],
    ownership: dict[str, Any],
    conflicts: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    ownership_path(root).parent.mkdir(parents=True, exist_ok=True)
    ownership_path(root).write_text(json.dumps(ownership, indent=2, sort_keys=True) + "\n")
    conflicts_path(root).write_text(
        json.dumps({"conflicts": conflicts, "warnings": warnings}, indent=2, sort_keys=True) + "\n"
    )
    lock = {
        "layers": [
            {
                "name": layer.get("name"),
                "repo": layer.get("repo"),
                "revision": layer.get("revision"),
                "commit": current_commit(layer_cache_path(root, layer["name"])),
                "enabled": layer.get("enabled", True),
                "mount": layer.get("mount", "/"),
            }
            for layer in manifest.get("layers", [])
        ]
    }
    lockfile_path(root).write_text(yaml.safe_dump(lock, sort_keys=False))


def ownership_entry(
    provider: Provider,
    masked: list[dict[str, Any]],
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    entry = {"visible": provider_entry(provider), "masked": masked}
    if reason:
        entry["reason"] = reason
    return entry


def hidden_ownership_entry(
    selected_layer: str,
    selected_mount: str,
    masked: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "visible": None,
        "selected_layer": selected_layer,
        "selected_mount": selected_mount,
        "hidden": True,
        "masked": masked,
        "reason": reason,
    }


def provider_entry(provider: Provider, *, visible: bool = True) -> dict[str, Any]:
    return {
        "layer": provider.layer_name,
        "repo": provider.repo,
        "commit": provider.commit,
        "source_path": provider.source_path,
        "mount": provider.mount,
        "path": provider.output_path or provider.source_path,
        "visible": visible,
    }


def conflict_provider(provider: Provider) -> dict[str, Any]:
    return {
        "layer": provider.layer_name,
        "layer_index": provider.layer_index + 1,
        "repo": provider.repo,
        "commit": provider.commit,
        "source_path": provider.source_path,
        "mount": provider.mount,
        "path": provider.output_path or provider.source_path,
    }
