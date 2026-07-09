from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any

import yaml

from .errors import LayerError

MANIFEST = "layer.yaml"
LOCKFILE = "layer.lock.yaml"


def default_manifest(output: str = "./buildtree", *, base_layer: bool = True) -> dict[str, Any]:
    layers = []
    workspace = {"output": output}
    if base_layer:
        workspace["write_layer"] = "workspace-base"
        layers.append({"name": "workspace-base", "kind": "local", "enabled": True})
    return {
        "workspace": workspace,
        "composition": {"same_path_policy": "top_wins"},
        "layers": layers,
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
        layer.setdefault("kind", "git" if layer.get("repo") else "local")
        layer["mount"] = normalize_mount(layer.get("mount"))
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


def normalize_mount(value: Any) -> str:
    if value is None:
        return "/"
    mount = str(value).strip()
    if mount in ("", ".", "/"):
        return "/"
    if re.match(r"^[A-Za-z]:", mount):
        raise LayerError(f"Invalid layer mount `{value}`: Windows drive paths are not allowed")
    if "\\" in mount:
        raise LayerError(f"Invalid layer mount `{value}`: use forward slashes")

    mount = re.sub(r"/+", "/", mount)
    if mount.startswith("/"):
        parts = [part for part in mount.split("/") if part]
        if parts and parts[0] in filesystem_root_names():
            raise LayerError(f"Invalid layer mount `{value}`: filesystem absolute paths are not allowed")
    else:
        parts = [part for part in mount.split("/") if part]

    if any(part == ".." for part in parts):
        raise LayerError(f"Invalid layer mount `{value}`: path traversal is not allowed")
    parts = [part for part in parts if part != "."]
    if not parts:
        return "/"
    normalized = PurePosixPath(*parts).as_posix()
    return f"/{normalized}"


def filesystem_root_names() -> set[str]:
    return {
        "bin",
        "boot",
        "dev",
        "etc",
        "home",
        "lib",
        "lib64",
        "mnt",
        "opt",
        "proc",
        "root",
        "run",
        "sbin",
        "srv",
        "sys",
        "tmp",
        "usr",
        "var",
    }


def buildtree_path_for_source(source_path: str, mount: str) -> str:
    mount = normalize_mount(mount)
    if mount == "/":
        return PurePosixPath(source_path).as_posix()
    return PurePosixPath(mount.lstrip("/"), source_path).as_posix()


def source_path_for_buildtree(buildtree_path: str, mount: str) -> str | None:
    mount = normalize_mount(mount)
    rel_path = PurePosixPath(buildtree_path).as_posix()
    if mount == "/":
        return rel_path
    prefix = mount.lstrip("/")
    if rel_path == prefix:
        return None
    if rel_path.startswith(prefix + "/"):
        return rel_path[len(prefix) + 1 :]
    return None
