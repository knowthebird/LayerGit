from __future__ import annotations

import filecmp
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import LayerError
from .gitops import ensure_local_layer_repo, is_git_repo, layer_cache_path, run_git, tracked_files
from .manifest import output_path, ownership_path, source_path_for_buildtree
from .reports import current_ownership, iter_output_files, load_json, visible_output_paths


def buildtree_diff(
    root: Path,
    manifest: dict[str, Any],
    *,
    path: str | None = None,
    layer: str | None = None,
    new_only: bool = False,
) -> dict[str, list[dict[str, str | None]]]:
    output = output_path(root, manifest)
    requested_path = normalize_buildtree_path(path, root, output) if path else None
    raw_ownership = load_json(ownership_path(root), {})
    ownership = current_ownership(raw_ownership, manifest)
    output_files = set(iter_output_files(output))
    current_visible = visible_output_paths(ownership)
    previously_owned = visible_output_paths(raw_ownership)
    write_layer = manifest.get("workspace", {}).get("write_layer")

    modified: list[dict[str, str | None]] = []
    deleted: list[dict[str, str | None]] = []
    stale: list[dict[str, str | None]] = []
    new: list[dict[str, str | None]] = []
    hidden: list[dict[str, str | None]] = []

    if not new_only:
        for rel_path, entry in sorted(ownership.items()):
            if requested_path and rel_path != requested_path:
                continue
            visible = entry.get("visible")
            if not visible:
                if entry.get("hidden") and not (output / rel_path).exists():
                    hidden.append(hidden_item(root, output, rel_path, entry))
                continue
            owner = visible.get("layer")
            if not owner:
                continue
            if layer and owner != layer:
                continue
            source_path = visible.get("source_path") or rel_path
            owner_cache = layer_cache_path(root, owner)
            source = owner_cache / source_path
            target = output / rel_path
            if (
                target.exists()
                and source.exists()
                and source_path not in tracked_files(owner_cache)
                and is_adopted_selection(manifest, rel_path, owner)
            ):
                new.append(untracked_provider_item(root, output, rel_path, owner, source_path))
                continue
            item = owned_item(root, output, rel_path, owner, source_path)
            if target.exists() and source.exists():
                if target.is_file() and source.is_file() and not filecmp.cmp(target, source, shallow=False):
                    modified.append(item)
            elif not target.exists() and source.exists():
                deleted.append(item)

        stale_paths = sorted((previously_owned - current_visible) & output_files)
        for rel_path in stale_paths:
            if requested_path and rel_path != requested_path:
                continue
            stale.append(
                {
                    "path": rel_path,
                    "buildtree_path": display_path(root, output / rel_path),
                    "layer": None,
                    "layer_path": None,
                }
            )

    for rel_path in sorted(output_files - previously_owned):
        if requested_path and rel_path != requested_path:
            continue
        target_write_layer = selected_write_layer_for_path(manifest, rel_path) or write_layer
        if layer and target_write_layer != layer:
            continue
        new.append(new_item(root, manifest, output, rel_path, target_write_layer))

    return {
        "modified": modified,
        "new": new,
        "deleted": deleted,
        "stale": stale,
        "hidden": hidden,
        "ignored": [],
    }


def apply_buildtree_changes(
    root: Path,
    manifest: dict[str, Any],
    diff: dict[str, list[dict[str, str | None]]],
    *,
    include_deleted: bool = False,
    dry_run: bool = False,
    stage_modified: bool = False,
    stage_new: bool = True,
) -> dict[str, list[dict[str, str | None]]]:
    applied_modified: list[dict[str, str | None]] = []
    applied_new: list[dict[str, str | None]] = []
    applied_deleted: list[dict[str, str | None]] = []
    skipped_deleted: list[dict[str, str | None]] = []
    skipped_hidden: list[dict[str, str | None]] = []

    for item in diff.get("hidden", []):
        skipped_hidden.append(item)

    for item in diff["new"]:
        if not item.get("write_layer"):
            rel_path = str(item["path"])
            raise LayerError(
                f"{rel_path} is not owned by any layer and no write layer is configured.\n\n"
                "Create or select a local layer:\n"
                "  layer add --local local-edits\n"
                "  layer write local-edits"
            )
        if not item.get("layer_path"):
            rel_path = str(item["path"])
            mount = item.get("mount") or "/"
            write_layer = item.get("write_layer")
            raise LayerError(
                f"Cannot apply {rel_path} to write layer {write_layer} because it is outside mount {mount}.\n"
                f"Use `layer write <layer>` or move the file under {mount}."
            )

    for item in diff["modified"]:
        copy_to_layer(root, item, dry_run=dry_run)
        result_item = item if dry_run else {**item, "staged": stage_modified}
        if stage_modified:
            stage_layer_file(root, manifest, item, dry_run=dry_run)
        applied_modified.append(result_item)

    for item in diff["new"]:
        copy_to_layer(root, item, dry_run=dry_run)
        result_item = item if dry_run else {**item, "staged": stage_new}
        if stage_new:
            stage_layer_file(root, manifest, item, dry_run=dry_run)
        applied_new.append(result_item)

    for item in diff["deleted"]:
        if not include_deleted:
            skipped_deleted.append(item)
            continue
        target = root / str(item["layer_path"])
        if not dry_run and target.exists() and target.is_file():
            target.unlink()
            remove_empty_parents(target.parent, layer_cache_path(root, str(item["layer"])))
        applied_deleted.append(item)

    return {
        "modified": applied_modified,
        "new": applied_new,
        "deleted": applied_deleted,
        "skipped_deleted": skipped_deleted,
        "skipped_hidden": skipped_hidden,
    }


def copy_to_layer(root: Path, item: dict[str, str | None], *, dry_run: bool) -> None:
    source = root / str(item["buildtree_path"])
    destination = root / str(item["layer_path"])
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def stage_layer_file(root: Path, manifest: dict[str, Any], item: dict[str, str | None], *, dry_run: bool) -> None:
    if dry_run:
        return
    layer_name = item.get("write_layer") or item.get("layer")
    source_path = item.get("source_path")
    if not layer_name or not source_path:
        return
    layer_name = str(layer_name)
    layer = next((candidate for candidate in manifest.get("layers", []) if candidate.get("name") == layer_name), {})
    cache = layer_cache_path(root, layer_name)
    if layer.get("kind") == "local":
        cache = ensure_local_layer_repo(root, layer_name)
    elif not is_git_repo(cache):
        raise LayerError(f"Layer {layer_name} cache is not a Git repository: {cache}")
    result = run_git(["add", "--", str(source_path)], cache, check=False)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"git add failed for {source_path}"
        raise LayerError(f"Copied {source_path} into layer {layer_name}, but staging failed: {message}")


def owned_item(root: Path, output: Path, rel_path: str, layer: str, source_path: str) -> dict[str, str | None]:
    layer_path = layer_cache_path(root, layer) / source_path
    return {
        "path": rel_path,
        "layer": layer,
        "source_path": source_path,
        "buildtree_path": display_path(root, output / rel_path),
        "layer_path": display_path(root, layer_path),
    }


def untracked_provider_item(root: Path, output: Path, rel_path: str, layer: str, source_path: str) -> dict[str, str | None]:
    layer_path = layer_cache_path(root, layer) / source_path
    return {
        "path": rel_path,
        "write_layer": layer,
        "mount": None,
        "source_path": source_path,
        "buildtree_path": display_path(root, output / rel_path),
        "layer_path": display_path(root, layer_path),
    }


def new_item(
    root: Path,
    manifest: dict[str, Any],
    output: Path,
    rel_path: str,
    write_layer: str | None,
) -> dict[str, str | None]:
    layer = next(
        (item for item in manifest.get("layers", []) if item.get("name") == write_layer),
        None,
    )
    mount = layer.get("mount", "/") if layer else "/"
    source_path = source_path_for_buildtree(rel_path, mount) if write_layer else None
    layer_path = layer_cache_path(root, write_layer) / source_path if write_layer and source_path else None
    return {
        "path": rel_path,
        "write_layer": write_layer,
        "mount": mount if write_layer else None,
        "source_path": source_path,
        "buildtree_path": display_path(root, output / rel_path),
        "layer_path": display_path(root, layer_path) if layer_path else None,
    }


def hidden_item(root: Path, output: Path, rel_path: str, entry: dict[str, Any]) -> dict[str, str | None]:
    return {
        "path": rel_path,
        "selected_layer": entry.get("selected_layer"),
        "mount": entry.get("selected_mount"),
        "reason": entry.get("reason"),
        "buildtree_path": display_path(root, output / rel_path),
        "layer_path": None,
    }


def selected_write_layer_for_path(manifest: dict[str, Any], rel_path: str) -> str | None:
    layer = (manifest.get("file_selection", {}).get(rel_path) or {}).get("layer")
    if not layer:
        return None
    enabled_layers = {
        item["name"]
        for item in manifest.get("layers", [])
        if item.get("enabled", True)
    }
    return layer if layer in enabled_layers else None


def is_adopted_selection(manifest: dict[str, Any], rel_path: str, layer: str) -> bool:
    rule = (manifest.get("file_selection", {}).get(rel_path) or {})
    return isinstance(rule, dict) and rule.get("layer") == layer and bool(rule.get("adopted"))


def normalize_buildtree_path(path: str, root: Path, output: Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            rel = candidate.resolve().relative_to(output.resolve()).as_posix()
        except ValueError as exc:
            raise LayerError(f"{path} is not inside the composed output tree") from exc
    else:
        rel = path.replace("\\", "/").removeprefix("./")
        output_rel = display_path(root, output).strip("/")
        if output_rel and (rel == output_rel or rel.startswith(output_rel + "/")):
            rel = rel[len(output_rel) :].lstrip("/")

    pure = PurePosixPath(rel)
    if not rel or rel == "." or pure.is_absolute() or ".." in pure.parts:
        raise LayerError(f"Invalid buildtree path: {path}")
    return pure.as_posix()


def display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def remove_empty_parents(path: Path, stop: Path) -> None:
    while path != stop and path.exists():
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent
