#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/layergit-overlap-demo.XXXXXX")"
export PYTHONDONTWRITEBYTECODE=1

cleanup() {
  printf '\nDemo files remain at: %s\n' "$tmp_root"
}
trap cleanup EXIT

if command -v layer >/dev/null 2>&1; then
  layer_cmd=(layer)
else
  layer_cmd=(python3 -m layergit.cli)
  export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
fi

make_repo() {
  local repo="$1"
  local util_text="$2"
  local readme_text="$3"

  mkdir -p "$repo/common" "$repo/src"
  git -C "$repo" init -b main >/dev/null
  printf '%s\n' "$util_text" > "$repo/common/util.c"
  printf '%s\n' "$readme_text" > "$repo/README.md"
  printf 'int value(void) { return %s; }\n' "$4" > "$repo/src/value.c"
  git -C "$repo" add .
  git -C "$repo" -c user.name="LayerGit Demo" -c user.email="demo@example.invalid" commit -m "Initial files" >/dev/null
}

workspace="$tmp_root/workspace"
repos="$tmp_root/repos"
mkdir -p "$workspace" "$repos"

make_repo "$repos/base" "base util" "Base layer" 1
make_repo "$repos/product" "product util" "Product layer" 2

printf 'Workspace: %s\n' "$workspace"
printf 'Repos:     %s\n\n' "$repos"

(
  cd "$workspace"

  "${layer_cmd[@]}" init --output ./buildtree
  "${layer_cmd[@]}" add "$repos/base" --name base
  "${layer_cmd[@]}" add "$repos/product" --name product

  printf '\nTop layer wins by default:\n'
  cat buildtree/common/util.c

  printf '\nProvenance for common/util.c:\n'
  "${layer_cmd[@]}" explain common/util.c

  printf '\nSelect the lower base layer for common/util.c:\n'
  "${layer_cmd[@]}" usefile base common/util.c

  printf '\nAfter layer usefile:\n'
  cat buildtree/common/util.c

  printf '\nUpdated provenance:\n'
  "${layer_cmd[@]}" explain common/util.c

  printf '\nExport composed result:\n'
  "${layer_cmd[@]}" export "$tmp_root/exported" --with-provenance
  find "$tmp_root/exported" -maxdepth 3 -type f | sort
)
