from __future__ import annotations

import fnmatch
import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .errors import LayerError
from .gitops import current_commit, layer_cache_path, sync_layer, tracked_files
from .manifest import conflicts_path, lockfile_path, output_path, ownership_path


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


def compose(root: Path, manifest: dict[str, Any], *, sync: bool = True) -> dict[str, Any]:
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
        if not source_root.exists():
            raise LayerError(f"Cache for layer `{layer['name']}` is missing: {source_root}")
        for rel_path, abs_path in iter_layer_files(source_root, layer):
            provider = Provider(
                layer_index=layer_index,
                layer_name=layer["name"],
                repo=layer.get("repo"),
                commit=current_commit(source_root),
                source_root=source_root,
                source_path=rel_path,
                abs_path=abs_path,
                overrides=tuple(layer.get("overrides") or ()),
            )
            providers_by_path.setdefault(rel_path, []).append(provider)

    ownership: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    visible: dict[str, Provider | None] = {}
    same_path_policy = manifest.get("composition", {}).get("same_path_policy", "top_wins")

    for rel_path in sorted(providers_by_path):
        selected_layer = selected_file_layer(manifest, rel_path)
        if selected_layer:
            providers = providers_by_path[rel_path]
            selected_provider = next(
                (provider for provider in providers if provider.layer_name == selected_layer),
                None,
            )
            if selected_provider is None:
                visible[rel_path] = None
                ownership[rel_path] = hidden_ownership_entry(
                    selected_layer,
                    [provider_entry(provider) for provider in reversed(providers)],
                    reason="selected layer does not provide this file; higher-layer files are hidden",
                )
                continue
            masked = [provider for provider in providers if provider.layer_name != selected_layer]
            visible[rel_path] = selected_provider
            ownership[rel_path] = ownership_entry(
                selected_provider,
                [provider_entry(provider) for provider in reversed(masked)],
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
            [provider_entry(provider) for provider in reversed(masked)],
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

    write_output_tree(output_path(root, manifest), visible)
    write_generated_files(root, manifest, ownership, conflicts, warnings)
    return {
        "visible_files": sum(1 for provider in visible.values() if provider is not None),
        "masked_files": sum(len(item.get("masked", [])) for item in ownership.values()),
        "conflicts": conflicts,
        "warnings": warnings,
        "ownership": ownership,
    }


def selected_file_layer(manifest: dict[str, Any], rel_path: str) -> str | None:
    rule = manifest.get("file_selection", {}).get(rel_path) or {}
    layer = rule.get("layer")
    if not layer:
        return None
    enabled_layers = {
        item["name"]
        for item in manifest.get("layers", [])
        if item.get("enabled", True)
    }
    return layer if layer in enabled_layers else None


def iter_layer_files(source_root: Path, layer: dict) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    include = tuple(layer.get("include") or ("**",))
    exclude = tuple(layer.get("exclude") or ())
    for rel in sorted(tracked_files(source_root)):
        path = source_root / rel
        if not path.is_file():
            continue
        if not any(path_matches(rel, pattern) for pattern in include):
            continue
        if any(path_matches(rel, pattern) for pattern in exclude):
            continue
        files.append((rel, path))
    return files


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
    for layer in manifest.get("layers", []):
        if not include_disabled and not layer.get("enabled", True):
            continue
        source_root = layer_cache_path(root, layer["name"])
        if rel_path not in tracked_files(source_root):
            continue
        source_file = source_root / rel_path
        if not source_file.is_file():
            continue
        include = tuple(layer.get("include") or ("**",))
        exclude = tuple(layer.get("exclude") or ())
        if not any(path_matches(rel_path, pattern) for pattern in include):
            continue
        if any(path_matches(rel_path, pattern) for pattern in exclude):
            continue
        providers.append(layer["name"])
    return providers


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
                        "source_path": rel_path,
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


def write_output_tree(output: Path, visible: dict[str, Provider | None]) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    for rel_path, provider in visible.items():
        if provider is None:
            continue
        target = output / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(provider.abs_path, target)


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
    masked: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "visible": None,
        "selected_layer": selected_layer,
        "hidden": True,
        "masked": masked,
        "reason": reason,
    }


def provider_entry(provider: Provider) -> dict[str, Any]:
    return {
        "layer": provider.layer_name,
        "repo": provider.repo,
        "commit": provider.commit,
        "source_path": provider.source_path,
    }


def conflict_provider(provider: Provider) -> dict[str, Any]:
    return {
        "layer": provider.layer_name,
        "layer_index": provider.layer_index + 1,
        "repo": provider.repo,
        "commit": provider.commit,
        "source_path": provider.source_path,
    }
