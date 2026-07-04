from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .composer import compose
from .errors import LayerError
from .gitops import init_repo_with_commit
from .manifest import lockfile_path, output_path, ownership_path


def export_workspace(
    root: Path,
    manifest: dict[str, Any],
    destination: Path,
    *,
    init_git: bool = False,
    with_provenance: bool = False,
) -> None:
    result = compose(root, manifest, sync=False)
    if result["conflicts"]:
        raise LayerError("Cannot export while duplicate-path conflicts exist.")

    source = output_path(root, manifest)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)

    if with_provenance:
        if ownership_path(root).exists():
            shutil.copy2(ownership_path(root), destination / ".layer-provenance.json")
        if lockfile_path(root).exists():
            shutil.copy2(lockfile_path(root), destination / ".layer-lock.yaml")

    if init_git:
        init_repo_with_commit(destination, "Export composed layered workspace")
