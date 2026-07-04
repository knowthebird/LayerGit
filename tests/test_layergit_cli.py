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

        result = self.run_layer("add", str(product), "--name", "product")

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
        duplicate = self.run_layer("add", str(product), "--name", "repo-a")

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
        self.assertEqual(self.run_layer("add", str(repo_b), "--name", "component-b").returncode, 0)

        result = self.run_layer("add", str(repo_c), "--name", "component-c")

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
        self.assertEqual(self.run_layer("add", str(repo_b), "--name", "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "--name", "component-c").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_d), "--name", "common", "--no-compose").returncode, 0)

        compose = self.run_layer("compose")
        self.assertEqual(compose.returncode, 0, compose.stdout + compose.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from d\n")

        explain = self.run_layer("explain", "common/util.c", "--json")
        self.assertEqual(explain.returncode, 0, explain.stderr)
        data = json.loads(explain.stdout)
        self.assertEqual(data["visible"]["layer"], "common")
        self.assertEqual({item["layer"] for item in data["masked"]}, {"component-b", "component-c"})
        self.assertEqual(data["reason"], "default top-layer-wins precedence")

    def test_sendtotop_sets_file_specific_precedence(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n", "src/b.c": "b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n", "src/c.c": "c\n"})
        repo_d = self.make_repo("repo-d", {"common/util.c": "from d\n", "src/d.c": "d\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "--name", "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "--name", "component-c").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_d), "--name", "common").returncode, 0)

        result = self.run_layer("sendtotop", "component-b", "common/util.c")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from b\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(
            manifest["file_precedence"]["common/util.c"]["order"],
            ["component-c", "common", "component-b"],
        )
        explain = self.run_layer("explain", "common/util.c", "--json")
        data = json.loads(explain.stdout)
        self.assertEqual(data["visible"]["layer"], "component-b")
        self.assertEqual(data["reason"], "file-specific precedence rule in layer.yaml")

    def test_usefile_selects_layer_for_file(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "--name", "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "--name", "component-c").returncode, 0)

        result = self.run_layer("usefile", "component-b", "common/util.c")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from b\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["file_selection"]["common/util.c"]["layer"], "component-b")
        explain = self.run_layer("explain", "common/util.c", "--json")
        data = json.loads(explain.stdout)
        self.assertEqual(data["visible"]["layer"], "component-b")
        self.assertEqual(data["masked"][0]["layer"], "component-c")
        self.assertEqual(data["reason"], "file-specific layer selection in layer.yaml")

        legacy_precedence = self.run_layer("sendtotop", "component-c", "common/util.c")

        self.assertEqual(legacy_precedence.returncode, 0, legacy_precedence.stdout + legacy_precedence.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from c\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertNotIn("file_selection", manifest)
        self.assertEqual(
            manifest["file_precedence"]["common/util.c"]["order"],
            ["component-b", "component-c"],
        )

    def test_usefile_can_hide_file_when_selected_layer_does_not_provide_it(self) -> None:
        base = self.make_repo("base", {"README.md": "base\n"})
        top = self.make_repo("top", {"common/util.c": "from top\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "--name", "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "--name", "top").returncode, 0)
        self.assertTrue((self.workspace / "buildtree" / "common" / "util.c").exists())

        result = self.run_layer("usefile", "base", "common/util.c")

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
        self.assertEqual(self.run_layer("add", str(repo_b), "--name", "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "--name", "component-c").returncode, 0)

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
        self.assertEqual(self.run_layer("add", str(repo_b), "--name", "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "--name", "component-c").returncode, 0)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from c\n")

        moved = self.run_layer("moveup", "component-b")

        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual([layer["name"] for layer in manifest["layers"]], ["component-c", "component-b"])
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from b\n")

        moved_back = self.run_layer("movedown", "component-b")

        self.assertEqual(moved_back.returncode, 0, moved_back.stdout + moved_back.stderr)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual([layer["name"] for layer in manifest["layers"]], ["component-b", "component-c"])
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from c\n")

    def test_composition_uses_tracked_files_only_by_default(self) -> None:
        product = self.make_repo("repo-a", {"tracked.txt": "tracked\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "--name", "product").returncode, 0)
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
        self.assertEqual(self.run_layer("add", str(product), "--name", "product").returncode, 0)

        result = self.run_layer("export", str(destination), "--with-provenance")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((destination / "src" / "main.c").exists())
        self.assertTrue((destination / ".layer-provenance.json").exists())
        self.assertTrue((destination / ".layer-lock.yaml").exists())

    def test_export_init_git_creates_standalone_repo_with_commit(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        destination = self.base / "merged-project"
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "--name", "product").returncode, 0)

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
        self.assertEqual(self.run_layer("add", str(product), "--name", "product").returncode, 0)

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

    def test_extension_json_list_tree_and_explain_are_valid(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "--name", "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "--name", "component-c").returncode, 0)

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
        self.assertEqual(self.run_layer("add", str(product), "--name", "product").returncode, 0)
        cached_file = self.workspace / ".layer" / "cache" / "product" / "src" / "main.c"
        cached_file.write_text("after\n")

        result = self.run_layer("pull", "--no-fetch")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "src" / "main.c").read_text(), "after\n")

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
