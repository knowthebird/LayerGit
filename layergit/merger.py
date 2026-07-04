from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .composer import compose
from .errors import LayerError
from .gitops import init_repo_with_commit, layer_cache_path


def merge_layers(
    root: Path,
    manifest: dict[str, Any],
    indexes: list[int],
    name: str,
    *,
    init_git: bool = False,
    with_provenance: bool = False,
) -> dict[str, Any]:
    if not indexes:
        raise LayerError("No layers selected for merge")
    if any(layer.get("name") == name for layer in manifest.get("layers", [])):
        raise LayerError(f"Layer `{name}` already exists")

    layers = manifest.get("layers", [])
    selected = [layers[index] for index in indexes]
    temp_manifest = {
        "workspace": {"output": f".layer/merge-tmp/{name}"},
        "layers": selected,
        "conflicts": manifest.get("conflicts", {"default": "error"}),
    }
    result = compose(root, temp_manifest, sync=False)
    if result["conflicts"]:
        raise LayerError("Cannot merge selected layers while conflicts exist.")

    target = layer_cache_path(root, name)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / ".layer" / "merge-tmp" / name, target)
    shutil.rmtree(root / ".layer" / "merge-tmp", ignore_errors=True)

    if with_provenance:
        provenance = target / ".layer-provenance.json"
        provenance.write_text((root / ".layer" / "ownership.json").read_text())
    if init_git:
        init_repo_with_commit(target, f"Merge layers into {name}")

    first = indexes[0]
    last = indexes[-1]
    new_layer = {"name": name, "repo": str(target)}
    manifest["layers"] = layers[:first] + [new_layer] + layers[last + 1 :]
    return manifest
