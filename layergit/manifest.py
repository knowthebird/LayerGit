from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import LayerError

MANIFEST = "layer.yaml"
LOCKFILE = "layer.lock.yaml"


def default_manifest(output: str = "./buildtree") -> dict[str, Any]:
    return {
        "workspace": {"output": output},
        "composition": {"same_path_policy": "top_wins"},
        "layers": [],
        "conflicts": {"duplicate_basename_policy": "warn"},
    }


def manifest_path(root: Path) -> Path:
    return root / MANIFEST


def load_manifest(root: Path) -> dict[str, Any]:
    path = manifest_path(root)
    if not path.exists():
        raise LayerError("No layer.yaml found. Run `layer init` first.")
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("workspace", {})
    data["workspace"].setdefault("output", "./buildtree")
    data.setdefault("composition", {})
    data["composition"].setdefault("same_path_policy", "top_wins")
    data.setdefault("layers", [])
    data.setdefault("conflicts", {})
    data["conflicts"].setdefault("duplicate_basename_policy", "warn")
    data.setdefault("file_precedence", {})
    for layer in data["layers"]:
        layer.setdefault("enabled", True)
    return data


def save_manifest(root: Path, manifest: dict[str, Any]) -> None:
    manifest_path(root).write_text(
        yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False)
    )


def output_path(root: Path, manifest: dict[str, Any]) -> Path:
    output = Path(manifest.get("workspace", {}).get("output", "./buildtree"))
    if not output.is_absolute():
        output = root / output
    return output


def layer_dir(root: Path) -> Path:
    return root / ".layer"


def cache_dir(root: Path) -> Path:
    return layer_dir(root) / "cache"


def ownership_path(root: Path) -> Path:
    return layer_dir(root) / "ownership.json"


def conflicts_path(root: Path) -> Path:
    return layer_dir(root) / "conflicts.json"


def lockfile_path(root: Path) -> Path:
    return root / LOCKFILE
