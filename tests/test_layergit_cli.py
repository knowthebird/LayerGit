from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class LayerGitCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.fixtures = self.base / "fixtures"
        self.fixtures.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_layer(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            [sys.executable, "-m", "layergit.cli", *args],
            cwd=cwd or self.workspace,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def make_repo(self, name: str, files: dict[str, str]) -> Path:
        repo = self.fixtures / name
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
        for rel_path, content in files.items():
            path = repo / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.PIPE)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test User",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                "initial",
            ],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
        )
        return repo

    def test_init_creates_workspace_files(self) -> None:
        result = self.run_layer("init", "--output", "./buildtree")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.workspace / "layer.yaml").exists())
        self.assertTrue((self.workspace / ".layer").exists())
        self.assertTrue((self.workspace / "buildtree").exists())
        gitignore = (self.workspace / ".gitignore").read_text()
        self.assertIn("# BEGIN LayerGit", gitignore)
        self.assertIn("/.layer/", gitignore)
        self.assertIn("/buildtree/", gitignore)
        self.assertIn("# END LayerGit", gitignore)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["composition"]["same_path_policy"], "top_wins")
        self.assertEqual(manifest["conflicts"]["duplicate_basename_policy"], "warn")

    def test_add_first_layer_composes_files(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "int main(void) { return 0; }\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)

        result = self.run_layer("add", str(product), "product")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.workspace / "buildtree" / "src" / "main.c").read_text(),
            "int main(void) { return 0; }\n",
        )
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["layers"][0]["name"], "product")
        self.assertTrue(manifest["layers"][0]["enabled"])

    def test_add_infers_layer_names_and_suffixes_collisions(self) -> None:
        product = self.make_repo("Repo A", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)

        first = self.run_layer("add", str(product))
        second = self.run_layer("add", str(product), "--no-compose")
        duplicate = self.run_layer("add", str(product), "repo-a")

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("Added layer 1: repo-a", first.stdout)
        self.assertIn("Added layer 2: repo-a-2", second.stdout)
        self.assertNotEqual(duplicate.returncode, 0)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual([layer["name"] for layer in manifest["layers"]], ["repo-a", "repo-a-2"])

    def test_overlapping_layers_default_to_top_wins_masking(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)

        result = self.run_layer("add", str(repo_c), "component-c")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from c\n")
        conflict_data = json.loads((self.workspace / ".layer" / "conflicts.json").read_text())
        self.assertEqual(conflict_data["conflicts"], [])
        explain = self.run_layer("explain", "common/util.c", "--json")
        data = json.loads(explain.stdout)
        self.assertEqual(data["visible"]["layer"], "component-c")
        self.assertEqual(data["masked"][0]["layer"], "component-b")
        self.assertEqual(data["reason"], "default top-layer-wins precedence")

    def test_top_wins_masks_lower_layers_and_explain_reports_provenance(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        repo_d = self.make_repo("repo-d", {"common/util.c": "from d\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_d), "common", "--no-compose").returncode, 0)

        compose = self.run_layer("compose")
        self.assertEqual(compose.returncode, 0, compose.stdout + compose.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from d\n")

        explain = self.run_layer("explain", "common/util.c", "--json")
        self.assertEqual(explain.returncode, 0, explain.stderr)
        data = json.loads(explain.stdout)
        self.assertEqual(data["visible"]["layer"], "common")
        self.assertEqual({item["layer"] for item in data["masked"]}, {"component-b", "component-c"})
        self.assertEqual(data["reason"], "default top-layer-wins precedence")

    def test_use_selects_layer_for_file(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n", "src/b.c": "b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n", "src/c.c": "c\n"})
        repo_d = self.make_repo("repo-d", {"common/util.c": "from d\n", "src/d.c": "d\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_d), "common").returncode, 0)

        result = self.run_layer("use", "common/util.c", "component-b")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from b\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["file_selection"]["common/util.c"]["layer"], "component-b")
        explain = self.run_layer("explain", "common/util.c", "--json")
        data = json.loads(explain.stdout)
        self.assertEqual(data["visible"]["layer"], "component-b")
        self.assertEqual(data["reason"], "file-specific layer selection in layer.yaml")

    def test_unuse_removes_file_selection(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)

        result = self.run_layer("use", "common/util.c", "component-b")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from b\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["file_selection"]["common/util.c"]["layer"], "component-b")
        explain = self.run_layer("explain", "common/util.c", "--json")
        data = json.loads(explain.stdout)
        self.assertEqual(data["visible"]["layer"], "component-b")
        self.assertEqual(data["masked"][0]["layer"], "component-c")
        self.assertEqual(data["reason"], "file-specific layer selection in layer.yaml")

        unuse = self.run_layer("unuse", "common/util.c")

        self.assertEqual(unuse.returncode, 0, unuse.stdout + unuse.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from c\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertNotIn("file_selection", manifest)

    def test_use_can_hide_file_when_selected_layer_does_not_provide_it(self) -> None:
        base = self.make_repo("base", {"README.md": "base\n"})
        top = self.make_repo("top", {"common/util.c": "from top\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        self.assertTrue((self.workspace / "buildtree" / "common" / "util.c").exists())

        result = self.run_layer("use", "common/util.c", "base")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.workspace / "buildtree" / "common" / "util.c").exists())
        tree = self.run_layer("tree", "--json")
        tree_data = json.loads(tree.stdout)
        hidden_file = next(item for item in tree_data["files"] if item["path"] == "common/util.c")
        self.assertTrue(hidden_file["hidden"])
        self.assertEqual(hidden_file["selectedLayer"], "base")
        self.assertIsNone(hidden_file["visibleLayer"])
        explain = self.run_layer("explain", "common/util.c", "--json")
        data = json.loads(explain.stdout)
        self.assertIsNone(data["visible"])
        self.assertTrue(data["hidden"])
        self.assertEqual(data["selected_layer"], "base")
        self.assertEqual(data["masked"][0]["layer"], "top")
        self.assertEqual(
            data["reason"],
            "selected layer does not provide this file; higher-layer files are hidden",
        )

    def test_disable_and_enable_layer_recomposes_without_deleting_cache(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)

        disabled = self.run_layer("disable", "component-c")

        self.assertEqual(disabled.returncode, 0, disabled.stdout + disabled.stderr)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertFalse(manifest["layers"][1]["enabled"])
        self.assertTrue((self.workspace / ".layer" / "cache" / "component-c").exists())
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from b\n")
        status = self.run_layer("status")
        self.assertIn("component-c      disabled", status.stdout)

        enabled = self.run_layer("enable", "component-c")

        self.assertEqual(enabled.returncode, 0, enabled.stdout + enabled.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from c\n")

    def test_move_layer_up_and_down_reorders_composition(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from c\n")

        moved = self.run_layer("move", "component-b", "top")

        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual([layer["name"] for layer in manifest["layers"]], ["component-c", "component-b"])
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from b\n")

        moved_back = self.run_layer("move", "component-b", "bottom")

        self.assertEqual(moved_back.returncode, 0, moved_back.stdout + moved_back.stderr)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual([layer["name"] for layer in manifest["layers"]], ["component-b", "component-c"])
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from c\n")

    def test_composition_uses_tracked_files_only_by_default(self) -> None:
        product = self.make_repo("repo-a", {"tracked.txt": "tracked\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        cached_untracked = self.workspace / ".layer" / "cache" / "product" / "personal-notes.txt"
        cached_untracked.write_text("local note\n")

        result = self.run_layer("pull", "--no-fetch")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.workspace / "buildtree" / "tracked.txt").exists())
        self.assertFalse((self.workspace / "buildtree" / "personal-notes.txt").exists())

    def test_export_with_provenance(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "int main(void) { return 0; }\n"})
        destination = self.base / "merged-project"
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)

        result = self.run_layer("export", str(destination), "--with-provenance")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((destination / "src" / "main.c").exists())
        self.assertTrue((destination / ".layer-provenance.json").exists())
        self.assertTrue((destination / ".layer-lock.yaml").exists())

    def test_export_init_git_creates_standalone_repo_with_commit(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        destination = self.base / "merged-project"
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)

        result = self.run_layer("export", str(destination), "--init-git")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((destination / ".git").exists())
        commit = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=destination,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(commit.returncode, 0, commit.stderr)
        self.assertEqual(commit.stdout.strip(), "Export composed layered workspace")

    def test_json_status_is_valid(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)

        result = self.run_layer("status", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["workspace"], str(self.workspace))
        self.assertEqual(data["output"], "./buildtree")
        self.assertEqual(data["layers"][0]["name"], "product")
        self.assertEqual(data["layers"][0]["position"], "top")
        self.assertFalse(data["layers"][0]["dirty"])
        self.assertIn("revision", data["layers"][0])
        self.assertEqual(data["composed_tree"]["visible_files"], 1)

    def test_status_ignores_stale_ownership_for_removed_layers(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)
        self.assertTrue((self.workspace / ".layer" / "ownership.json").exists())

        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        manifest["layers"] = []
        (self.workspace / "layer.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))

        status = self.run_layer("status", "--json")
        tree = self.run_layer("tree", "--json")
        explain = self.run_layer("explain", "common/util.c", "--json")

        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(tree.returncode, 0, tree.stderr)
        self.assertEqual(explain.returncode, 0)
        status_data = json.loads(status.stdout)
        tree_data = json.loads(tree.stdout)
        self.assertEqual(status_data["layers"], [])
        self.assertEqual(status_data["composed_tree"]["visible_files"], 0)
        self.assertEqual(status_data["composed_tree"]["masked_files"], 0)
        self.assertEqual(status_data["composed_tree"]["stale_owned_files"], 1)
        self.assertEqual(status_data["composed_tree"]["untracked_files"], 0)
        self.assertEqual(tree_data["files"][0]["path"], "common/util.c")
        self.assertEqual(tree_data["files"][0]["ownership"], "stale")
        explain_data = json.loads(explain.stdout)
        self.assertTrue(explain_data["unowned"])
        self.assertTrue(explain_data["stale_owned"])

    def test_status_tree_and_explain_report_untracked_buildtree_files(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        untracked = self.workspace / "buildtree" / "build" / "output.bin"
        untracked.parent.mkdir(parents=True)
        untracked.write_text("artifact\n")

        status = self.run_layer("status", "--json")
        tree = self.run_layer("tree", "--json")
        explain = self.run_layer("explain", "build/output.bin", "--json")

        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(tree.returncode, 0, tree.stderr)
        self.assertEqual(explain.returncode, 0, explain.stderr)
        status_data = json.loads(status.stdout)
        tree_data = json.loads(tree.stdout)
        explain_data = json.loads(explain.stdout)
        self.assertEqual(status_data["composed_tree"]["visible_files"], 1)
        self.assertEqual(status_data["composed_tree"]["untracked_files"], 1)
        untracked_item = next(item for item in tree_data["files"] if item["path"] == "build/output.bin")
        self.assertFalse(untracked_item["owned"])
        self.assertEqual(untracked_item["ownership"], "untracked")
        self.assertTrue(explain_data["unowned"])
        self.assertTrue(explain_data["untracked"])
        self.assertEqual(
            explain_data["reason"],
            "file exists in buildtree but is not owned by any layer",
        )

    def test_compose_preserves_untracked_buildtree_files_by_default(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        untracked = self.workspace / "buildtree" / "build" / "output.bin"
        untracked.parent.mkdir(parents=True)
        untracked.write_text("artifact\n")

        result = self.run_layer("compose")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(untracked.exists())

    def test_compose_removes_stale_owned_buildtree_files_by_default(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        composed_file = self.workspace / "buildtree" / "src" / "main.c"
        self.assertTrue(composed_file.exists())
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        manifest["layers"] = []
        (self.workspace / "layer.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))

        result = self.run_layer("compose")
        status = self.run_layer("status", "--json")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(composed_file.exists())
        status_data = json.loads(status.stdout)
        self.assertEqual(status_data["composed_tree"]["visible_files"], 0)
        self.assertEqual(status_data["composed_tree"]["untracked_files"], 0)
        self.assertEqual(status_data["composed_tree"]["stale_owned_files"], 0)

    def test_disabling_only_provider_removes_previously_owned_output(self) -> None:
        base = self.make_repo("base", {"main.c": "base\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        output_file = self.workspace / "buildtree" / "main.c"
        self.assertTrue(output_file.exists())

        disabled = self.run_layer("disable", "base")
        status = self.run_layer("status", "--json")

        self.assertEqual(disabled.returncode, 0, disabled.stdout + disabled.stderr)
        self.assertFalse(output_file.exists())
        status_data = json.loads(status.stdout)
        self.assertEqual(status_data["composed_tree"]["visible_files"], 0)
        self.assertEqual(status_data["composed_tree"]["untracked_files"], 0)
        self.assertEqual(status_data["composed_tree"]["stale_owned_files"], 0)

    def test_compose_clean_removes_untracked_buildtree_files(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        untracked = self.workspace / "buildtree" / "build" / "output.bin"
        untracked.parent.mkdir(parents=True)
        untracked.write_text("artifact\n")

        result = self.run_layer("compose", "--clean")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(untracked.exists())
        self.assertTrue((self.workspace / "buildtree" / "src" / "main.c").exists())

    def test_extension_json_list_tree_and_explain_are_valid(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)

        layer_list = self.run_layer("list", "--json")
        tree = self.run_layer("tree", "--json")
        explain = self.run_layer("explain", "common/util.c", "--json")

        self.assertEqual(layer_list.returncode, 0, layer_list.stderr)
        self.assertEqual(tree.returncode, 0, tree.stderr)
        self.assertEqual(explain.returncode, 0, explain.stderr)
        list_data = json.loads(layer_list.stdout)
        tree_data = json.loads(tree.stdout)
        explain_data = json.loads(explain.stdout)
        self.assertEqual([layer["name"] for layer in list_data["layers"]], ["component-b", "component-c"])
        self.assertEqual(tree_data["files"][0]["path"], "common/util.c")
        self.assertEqual(tree_data["files"][0]["visibleLayer"], "component-c")
        self.assertEqual(tree_data["files"][0]["maskedByThisFile"], ["component-b"])
        self.assertEqual(explain_data["path"], "common/util.c")
        self.assertEqual(explain_data["visible"]["layerIndex"], 2)
        self.assertEqual(explain_data["visible"]["sourcePath"], "common/util.c")
        self.assertEqual(explain_data["masked"][0]["layerIndex"], 1)

    def test_pull_no_fetch_recomposes_cached_layer_without_remote_fetch(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "before\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        cached_file = self.workspace / ".layer" / "cache" / "product" / "src" / "main.c"
        cached_file.write_text("after\n")

        result = self.run_layer("pull", "--no-fetch")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "src" / "main.c").read_text(), "after\n")

    def test_layer_scoped_git_uses_global_layer_selector(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "before\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)

        status = self.run_layer("-L", "product", "git", "status", "--short")

        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout, "")

        cached_file = self.workspace / ".layer" / "cache" / "product" / "new.txt"
        cached_file.write_text("new\n")
        added = self.run_layer("-L", "product", "git", "add", "new.txt")
        status_after_add = self.run_layer("-L", "product", "git", "status", "--short")

        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("A  new.txt", status_after_add.stdout)

    def test_git_without_layer_selector_errors_cleanly(self) -> None:
        self.assertEqual(self.run_layer("init").returncode, 0)

        result = self.run_layer("git", "status")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("layer: git requires a layer. Use: layer -L <layer> git <git-args>", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_command_uses_git_like_error(self) -> None:
        result = self.run_layer("askjdn")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            result.stderr.strip(),
            "layer: 'askjdn' is not a layer command. See 'layer --help'.",
        )
        self.assertNotIn("Traceback", result.stderr)

    def test_help_shows_new_command_shape_without_old_public_commands(self) -> None:
        result = self.run_layer("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: layer [-h] [-L <layer>] <command> [<args>]", result.stdout)
        self.assertIn("These are common LayerGit commands used in various situations:", result.stdout)
        self.assertIn("Workspace:", result.stdout)
        self.assertIn("add <repo> [name]", result.stdout)
        self.assertIn("move <layer> <pos>", result.stdout)
        self.assertIn("layer move repoa up", result.stdout)
        self.assertIn("compose --clean", result.stdout)
        self.assertIn("layer compose --clean", result.stdout)
        self.assertIn("use <file> <layer>", result.stdout)
        self.assertIn("-L, --layer <layer>", result.stdout)
        self.assertIn("See 'layer help <command>'", result.stdout)
        self.assertNotIn("positional arguments:", result.stdout)
        self.assertNotIn("{init,add,remove", result.stdout)
        self.assertNotIn("usefile", result.stdout)
        self.assertNotIn("moveup", result.stdout)
        self.assertNotIn("sendlayertotop", result.stdout)

    def test_help_command_shows_command_specific_help(self) -> None:
        result = self.run_layer("help", "status")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: layer status", result.stdout)
        self.assertIn("--json", result.stdout)

    def test_compose_help_mentions_clean_behavior(self) -> None:
        result = self.run_layer("help", "compose")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: layer compose", result.stdout)
        self.assertIn("--clean", result.stdout)
        self.assertIn("preserves untracked buildtree files", result.stdout)
        self.assertIn("including untracked", result.stdout)
        self.assertIn("buildtree files", result.stdout)

    def test_move_help_makes_argument_order_explicit(self) -> None:
        result = self.run_layer("help", "move")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: layer move <layer> <top|bottom|up|down|before|after> [target-layer]", result.stdout)
        self.assertIn("The command is `layer move <layer> <position>`.", result.stdout)
        self.assertIn("layer move layera up", result.stdout)
        self.assertNotIn("layer layera move", result.stdout)

    def test_pyproject_exposes_layer_and_layergit_scripts(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text())

        self.assertEqual(data["project"]["scripts"]["layer"], "layergit.cli:main")
        self.assertEqual(data["project"]["scripts"]["layergit"], "layergit.cli:main")

    def test_readme_uses_layer_as_primary_command(self) -> None:
        readme = (ROOT / "README.md").read_text()

        self.assertIn("layer init --output ./buildtree", readme)
        self.assertIn("python3 -m layergit.cli status", readme)

    def test_vscode_extension_scaffold_matches_mvp_shape(self) -> None:
        package = json.loads((ROOT / "vscode-extension" / "package.json").read_text())
        views = package["contributes"]["views"]["layergit"]
        commands = {item["command"] for item in package["contributes"]["commands"]}

        self.assertEqual(package["contributes"]["configuration"]["properties"]["layergit.command"]["default"], "layer")
        self.assertEqual(package["contributes"]["viewsContainers"]["activitybar"][0]["id"], "layergit")
        self.assertEqual({view["id"] for view in views}, {"layergit.layers", "layergit.composedTree"})
        self.assertIn("layergit.refresh", commands)
        self.assertIn("layergit.init", commands)
        self.assertIn("layergit.explainCurrentFile", commands)
        self.assertIn("layergit.moveLayerUp", commands)
        self.assertIn("layergit.moveLayerDown", commands)
        self.assertIn("layergit.sendLayerToTop", commands)
        self.assertIn("layergit.sendLayerToBottom", commands)
        self.assertIn("layergit.useLayerForFile", commands)
        self.assertTrue((ROOT / "vscode-extension" / "src" / "cli.ts").exists())
        self.assertTrue((ROOT / "vscode-extension" / "src" / "layersView.ts").exists())
        self.assertTrue((ROOT / "vscode-extension" / "src" / "composedTreeView.ts").exists())


if __name__ == "__main__":
    unittest.main()
