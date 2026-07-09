from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .errors import LayerError
from .manifest import cache_dir


def run_git(
    args: list[str],
    cwd: Path,
    *,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=check,
    )


def is_git_repo(path: Path) -> bool:
    try:
        result = run_git(["rev-parse", "--show-toplevel"], path, check=False)
    except FileNotFoundError as exc:
        raise LayerError("git executable was not found") from exc
    if result.returncode != 0:
        return False
    try:
        return Path(result.stdout.strip()).resolve() == path.resolve()
    except OSError:
        return False


def layer_cache_path(root: Path, name: str) -> Path:
    return cache_dir(root) / name


def sync_layer(root: Path, layer: dict, *, clone_only: bool = False) -> None:
    name = layer["name"]
    if layer.get("kind") == "local":
        ensure_local_layer_repo(root, name)
        return
    repo = layer.get("repo")
    revision = layer.get("revision")
    target = layer_cache_path(root, name)
    cache_dir(root).mkdir(parents=True, exist_ok=True)

    if target.exists():
        if is_git_repo(target):
            if not clone_only:
                run_git(["fetch", "--all", "--prune"], target)
                if revision:
                    run_git(["checkout", revision], target)
                pull = run_git(["pull", "--ff-only"], target, check=False)
                if pull.returncode != 0 and "no tracking information" not in pull.stderr.lower():
                    raise LayerError(pull.stderr.strip())
        return

    if not repo:
        raise LayerError(f"Layer {name} has no repo path")

    result = subprocess.run(
        ["git", "clone", repo, str(target)],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise LayerError(result.stderr.strip())
    if revision:
        run_git(["checkout", revision], target)


def ensure_local_layer_repo(root: Path, name: str) -> Path:
    target = layer_cache_path(root, name)
    target.mkdir(parents=True, exist_ok=True)
    if not is_git_repo(target):
        run_git(["init"], target)
    return target


def remove_cache(root: Path, name: str) -> None:
    target = layer_cache_path(root, name)
    if target.exists():
        shutil.rmtree(target)


def current_commit(path: Path) -> str | None:
    if not path.exists() or not is_git_repo(path):
        return None
    result = run_git(["rev-parse", "--short", "HEAD"], path, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def current_branch(path: Path) -> str | None:
    if not path.exists() or not is_git_repo(path):
        return None
    result = run_git(["branch", "--show-current"], path, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or "detached"


def porcelain_status(path: Path) -> str:
    if not path.exists() or not is_git_repo(path):
        return "missing"
    result = run_git(["status", "--porcelain"], path, check=False)
    if result.returncode != 0:
        return "error"
    return "modified" if result.stdout.strip() else "clean"


def tracked_files(path: Path) -> list[str]:
    if not path.exists() or not is_git_repo(path):
        return []
    result = run_git(["ls-files"], path, check=False)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def init_repo_with_commit(path: Path, message: str) -> None:
    run_git(["init"], path)
    run_git(["add", "."], path)
    status = run_git(["status", "--porcelain"], path)
    if status.stdout.strip():
        env_cmd = ["-c", "user.name=LayerGit", "-c", "user.email=layergit@example.invalid"]
        run_git([*env_cmd, "commit", "-m", message], path)
