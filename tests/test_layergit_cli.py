from __future__ import annotations

import json
import os
import shutil
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
        command = [sys.executable, "-m", "layergit.cli", *args]
        if env.get("LAYERGIT_TEST_COVERAGE") == "1":
            env["COVERAGE_FILE"] = str(ROOT / ".coverage")
            command = [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--parallel-mode",
                "-m",
                "layergit.cli",
                *args,
            ]
        return subprocess.run(
            command,
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

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text())

    def ownership(self) -> dict:
        return self.read_json(self.workspace / ".layer" / "ownership.json")

    def status_json(self) -> dict:
        result = self.run_layer("status", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def tree_json(self) -> dict:
        result = self.run_layer("tree", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_current_ownership_matches_buildtree(self, disabled_layers: set[str] | None = None) -> None:
        disabled_layers = disabled_layers or set()
        ownership = self.ownership()
        tree = self.tree_json()
        output_files = {
            path.relative_to(self.workspace / "buildtree").as_posix()
            for path in (self.workspace / "buildtree").rglob("*")
            if path.is_file()
        }
        visible_owned = {
            rel_path: entry["visible"]
            for rel_path, entry in ownership.items()
            if entry.get("visible") is not None
        }
        tree_visible = {
            item["path"]: item
            for item in tree["files"]
            if item.get("owned") and item.get("ownership") == "composed" and item.get("visibleLayer")
        }

        self.assertEqual(set(visible_owned), output_files & set(visible_owned))
        self.assertEqual(set(tree_visible), set(visible_owned))
        for rel_path, visible in visible_owned.items():
            self.assertNotIn(visible["layer"], disabled_layers)
            self.assertEqual(tree_visible[rel_path]["visibleLayer"], visible["layer"])
            self.assertNotIn(visible["layer"], {item["layer"] for item in ownership[rel_path].get("masked", [])})

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
        self.assertEqual(manifest["workspace"]["write_layer"], "workspace-base")
        self.assertEqual(
            manifest["layers"],
            [{"name": "workspace-base", "kind": "local", "enabled": True}],
        )
        self.assertTrue((self.workspace / ".layer" / "cache" / "workspace-base" / ".git").exists())

    def test_init_no_base_layer_starts_empty(self) -> None:
        result = self.run_layer("init", "--output", "./buildtree", "--no-base-layer")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.workspace / ".layer" / "cache" / "workspace-base").exists())
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertNotIn("write_layer", manifest["workspace"])
        self.assertEqual(manifest["layers"], [])

    def test_add_first_layer_composes_files(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "int main(void) { return 0; }\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)

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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)

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

    def test_default_workspace_base_is_local_and_git_backed(self) -> None:
        self.assertEqual(self.run_layer("init").returncode, 0)

        status = self.run_layer("status", "--json")
        git_status = self.run_layer("-L", "workspace-base", "git", "status", "--short")

        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(git_status.returncode, 0, git_status.stderr)
        data = json.loads(status.stdout)
        self.assertEqual(data["write_layer"], "workspace-base")
        self.assertEqual(data["layers"][0]["name"], "workspace-base")
        self.assertEqual(data["layers"][0]["kind"], "local")
        self.assertTrue(data["layers"][0]["enabled"])

    def test_status_default_output_shows_stack_top_to_bottom(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        cached_file = self.workspace / ".layer" / "cache" / "product" / "src" / "main.c"
        cached_file.write_text("dirty\n")

        result = self.run_layer("status")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LayerGit status", result.stdout)
        self.assertIn(f"Workspace: {self.workspace}", result.stdout)
        self.assertIn("Output:    ./buildtree", result.stdout)
        self.assertIn("Order:     top -> bottom; higher layers win path conflicts", result.stdout)
        self.assertIn("Write:     workspace-base", result.stdout)
        self.assertIn("ORDER", result.stdout)
        table_lines = [line for line in result.stdout.splitlines() if line.strip()[:1].isdigit()]
        product_line = next(line for line in table_lines if "product" in line)
        base_line = next(line for line in table_lines if "workspace-base" in line)
        self.assertLess(result.stdout.index(product_line), result.stdout.index(base_line))
        self.assertRegex(product_line, r"^\s*2\s+product\s+git\s+/\s+enabled\s+dirty\s+main\s+[0-9a-f]+\s+top$")
        self.assertIn("1", base_line)
        self.assertIn("workspace-base", base_line)
        self.assertIn("local", base_line)
        self.assertIn("enabled", base_line)
        self.assertIn("clean", base_line)
        self.assertIn("no commits", base_line)
        self.assertIn("write-layer, bottom", base_line)
        self.assertIn("  2 layers enabled", result.stdout)
        self.assertIn("  0 disabled", result.stdout)
        self.assertIn("  1 dirty", result.stdout)
        self.assertIn("  0 conflicts", result.stdout)

    def test_status_short_preserves_compact_output(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)

        result = self.run_layer("status", "--short")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Layers:", result.stdout)
        self.assertIn("Write layer: workspace-base", result.stdout)
        self.assertIn("Buildtree:", result.stdout)

    def test_git_layers_added_after_default_base_are_above_it(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)

        result = self.run_layer("add", str(product), "product")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual([layer["name"] for layer in manifest["layers"]], ["workspace-base", "product"])
        self.assertEqual([layer["kind"] for layer in manifest["layers"]], ["local", "git"])

    def test_add_local_creates_git_backed_local_layer(self) -> None:
        self.assertEqual(self.run_layer("init").returncode, 0)

        result = self.run_layer("add", "--local", "local-edits")
        git_status = self.run_layer("-L", "local-edits", "git", "status", "--short")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(git_status.returncode, 0, git_status.stderr)
        self.assertTrue((self.workspace / ".layer" / "cache" / "local-edits" / ".git").exists())
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["layers"][-1]["name"], "local-edits")
        self.assertEqual(manifest["layers"][-1]["kind"], "local")

    def test_local_layer_participates_in_top_wins_composition(self) -> None:
        repo = self.make_repo("repo-a", {"common/util.c": "from repo\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo), "repo-a").returncode, 0)
        self.assertEqual(self.run_layer("add", "--local", "local-edits").returncode, 0)
        local_file = self.workspace / ".layer" / "cache" / "local-edits" / "common" / "util.c"
        local_file.parent.mkdir(parents=True)
        local_file.write_text("from local\n")
        self.assertEqual(self.run_layer("-L", "local-edits", "git", "add", ".").returncode, 0)

        result = self.run_layer("compose")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from local\n")
        explain = json.loads(self.run_layer("explain", "common/util.c", "--json").stdout)
        self.assertEqual(explain["visible"]["layer"], "local-edits")

    def test_write_layer_command_updates_manifest(self) -> None:
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", "--local", "local-edits").returncode, 0)

        result = self.run_layer("write", "local-edits")
        missing = self.run_layer("write", "missing-layer")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["workspace"]["write_layer"], "local-edits")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("No layer matches selector", missing.stderr)

    def test_overlapping_layers_default_to_top_wins_masking(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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

    def test_overlaps_reports_visible_and_masked_providers(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n", "common/other.c": "other b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n", "common/other.c": "other c\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)

        result = self.run_layer("overlaps")
        data_result = self.run_layer("overlaps", "--json")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Overlapping paths:", result.stdout)
        self.assertIn("common/util.c", result.stdout)
        self.assertIn("visible: component-c", result.stdout)
        self.assertIn("masked:  component-b", result.stdout)
        self.assertIn("reason:  top-layer-wins", result.stdout)
        self.assertEqual(data_result.returncode, 0, data_result.stderr)
        data = json.loads(data_result.stdout)
        self.assertFalse(data["stale"])
        by_path = {item["path"]: item for item in data["overlaps"]}
        self.assertEqual(set(by_path), {"common/other.c", "common/util.c"})
        self.assertEqual(by_path["common/util.c"]["visible"]["layer"], "component-c")
        self.assertEqual(by_path["common/util.c"]["masked"][0]["layer"], "component-b")
        self.assertEqual(by_path["common/util.c"]["reason"], "top-layer-wins")

        filtered = self.run_layer("overlaps", "common/util.c", "--json")
        self.assertEqual(filtered.returncode, 0, filtered.stderr)
        filtered_data = json.loads(filtered.stdout)
        self.assertEqual([item["path"] for item in filtered_data["overlaps"]], ["common/util.c"])

    def test_overlaps_reports_no_overlaps(self) -> None:
        repo_b = self.make_repo("repo-b", {"src/b.c": "b\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)

        result = self.run_layer("overlaps")
        path_result = self.run_layer("overlaps", "src/b.c")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "No overlapping paths.")
        self.assertEqual(path_result.returncode, 0, path_result.stdout + path_result.stderr)
        self.assertEqual(path_result.stdout.strip(), "No overlapping paths for src/b.c.")

    def test_overlaps_reports_layer_use_and_ignores_disabled_layers(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)
        self.assertEqual(self.run_layer("use", "common/util.c", "component-b").returncode, 0)

        selected = self.run_layer("overlaps", "common/util.c", "--json")
        self.assertEqual(selected.returncode, 0, selected.stderr)
        selected_data = json.loads(selected.stdout)
        self.assertEqual(selected_data["overlaps"][0]["visible"]["layer"], "component-b")
        self.assertEqual(selected_data["overlaps"][0]["masked"][0]["layer"], "component-c")
        self.assertEqual(selected_data["overlaps"][0]["reason"], "selected by layer use")

        self.assertEqual(self.run_layer("disable", "component-c").returncode, 0)
        disabled = self.run_layer("overlaps", "common/util.c", "--json")

        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        disabled_data = json.loads(disabled.stdout)
        self.assertEqual(disabled_data["overlaps"], [])

    def test_mount_paths_compose_and_report_provenance(self) -> None:
        app = self.make_repo("app", {"src/main.c": "app\n"})
        docs = self.make_repo("docs", {"README.md": "docs\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)

        app_add = self.run_layer("add", str(app), "app", "--mount", "app")
        docs_add = self.run_layer("add", str(docs), "docs", "--mount", "/docs//")

        self.assertEqual(app_add.returncode, 0, app_add.stdout + app_add.stderr)
        self.assertEqual(docs_add.returncode, 0, docs_add.stdout + docs_add.stderr)
        self.assertEqual((self.workspace / "buildtree" / "app" / "src" / "main.c").read_text(), "app\n")
        self.assertEqual((self.workspace / "buildtree" / "docs" / "README.md").read_text(), "docs\n")
        self.assertFalse((self.workspace / "buildtree" / "src" / "main.c").exists())
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual([layer["mount"] for layer in manifest["layers"]], ["/app", "/docs"])

        ownership = self.ownership()["app/src/main.c"]
        self.assertEqual(ownership["visible"]["layer"], "app")
        self.assertEqual(ownership["visible"]["source_path"], "src/main.c")
        self.assertEqual(ownership["visible"]["mount"], "/app")
        self.assertTrue(ownership["visible"]["visible"])
        explain = self.run_layer("explain", "app/src/main.c", "--json")
        self.assertEqual(explain.returncode, 0, explain.stderr)
        explain_data = json.loads(explain.stdout)
        self.assertEqual(explain_data["visible"]["sourcePath"], "src/main.c")
        self.assertEqual(explain_data["visible"]["mount"], "/app")

        status = self.run_layer("status")
        status_json = self.status_json()
        tree = self.tree_json()
        self.assertIn("MOUNT", status.stdout)
        self.assertEqual(status_json["layers"][0]["mount"], "/app")
        app_tree = next(item for item in tree["files"] if item["path"] == "app/src/main.c")
        self.assertEqual(app_tree["sourcePath"], "src/main.c")
        self.assertEqual(app_tree["mount"], "/app")

    def test_mounted_overlaps_use_and_distinct_mounts(self) -> None:
        repo_a = self.make_repo("repo-a", {"gpio.c": "a\n"})
        repo_b = self.make_repo("repo-b", {"gpio.c": "b\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_a), "a", "--mount", "/drivers").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "b", "--mount", "/drivers").returncode, 0)

        self.assertEqual((self.workspace / "buildtree" / "drivers" / "gpio.c").read_text(), "b\n")
        overlaps = json.loads(self.run_layer("overlaps", "drivers/gpio.c", "--json").stdout)
        self.assertEqual(overlaps["overlaps"][0]["visible"]["layer"], "b")
        self.assertEqual(overlaps["overlaps"][0]["visible"]["source_path"], "gpio.c")
        self.assertEqual(overlaps["overlaps"][0]["visible"]["mount"], "/drivers")
        self.assertTrue(overlaps["overlaps"][0]["visible"]["visible"])
        self.assertEqual(overlaps["overlaps"][0]["masked"][0]["layer"], "a")
        self.assertFalse(overlaps["overlaps"][0]["masked"][0]["visible"])

        selected = self.run_layer("use", "drivers/gpio.c", "a")
        self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
        self.assertEqual((self.workspace / "buildtree" / "drivers" / "gpio.c").read_text(), "a\n")
        other_workspace = self.base / "workspace-distinct"
        other_workspace.mkdir()
        self.assertEqual(self.run_layer("init", "--no-base-layer", cwd=other_workspace).returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_a), "a", "--mount", "/drivers/a", cwd=other_workspace).returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "b", "--mount", "/drivers/b", cwd=other_workspace).returncode, 0)
        distinct = self.run_layer("overlaps", "--json", cwd=other_workspace)
        self.assertEqual(json.loads(distinct.stdout)["overlaps"], [])

    def test_apply_uses_mounted_source_path_for_owned_and_new_files(self) -> None:
        app = self.make_repo("app", {"src/main.c": "before\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(app), "app", "--mount", "/app").returncode, 0)
        self.assertEqual(self.run_layer("write", "app").returncode, 0)
        (self.workspace / "buildtree" / "app" / "src" / "main.c").write_text("after\n")
        new_file = self.workspace / "buildtree" / "app" / "new.c"
        new_file.write_text("new\n")
        outside = self.workspace / "buildtree" / "docs" / "readme.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("docs\n")

        owned = self.run_layer("apply", "app/src/main.c")
        new = self.run_layer("apply", "app/new.c")
        outside_result = self.run_layer("apply", "docs/readme.md")

        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        self.assertEqual((self.workspace / ".layer" / "cache" / "app" / "src" / "main.c").read_text(), "after\n")
        self.assertEqual((self.workspace / ".layer" / "cache" / "app" / "new.c").read_text(), "new\n")
        self.assertFalse((self.workspace / ".layer" / "cache" / "app" / "app" / "new.c").exists())
        self.assertNotEqual(outside_result.returncode, 0)
        self.assertIn("outside mount /app", outside_result.stderr)

    def test_adopt_uses_mounted_source_path_and_rejects_outside_mount(self) -> None:
        app = self.make_repo("app", {"src/main.c": "before\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(app), "app", "--mount", "/app").returncode, 0)
        new_file = self.workspace / "buildtree" / "app" / "new.c"
        new_file.write_text("new\n")
        outside = self.workspace / "buildtree" / "docs" / "readme.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("docs\n")

        adopted = self.run_layer("adopt", "app/new.c", "app")
        outside_result = self.run_layer("adopt", "docs/readme.md", "app")

        self.assertEqual(adopted.returncode, 0, adopted.stdout + adopted.stderr)
        self.assertEqual((self.workspace / ".layer" / "cache" / "app" / "new.c").read_text(), "new\n")
        self.assertFalse((self.workspace / ".layer" / "cache" / "app" / "app" / "new.c").exists())
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["file_selection"]["app/new.c"]["layer"], "app")
        self.assertTrue(manifest["file_selection"]["app/new.c"]["adopted"])
        self.assertNotEqual(outside_result.returncode, 0)
        self.assertIn("outside mount /app", outside_result.stderr)

    def test_invalid_mounts_are_rejected(self) -> None:
        repo = self.make_repo("repo", {"file.c": "ok\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        for bad_mount in ("../outside", "/tmp/outside", "C:\\temp", "app/../../outside"):
            result = self.run_layer("add", str(repo), f"bad-{len(bad_mount)}", "--mount", bad_mount)
            self.assertNotEqual(result.returncode, 0, bad_mount)
            self.assertIn("Invalid layer mount", result.stderr)

    def test_mounted_layer_disable_removes_owned_files_and_preserves_unowned_files(self) -> None:
        app = self.make_repo("app", {"src/main.c": "app\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(app), "app", "--mount", "/app").returncode, 0)
        unowned = self.workspace / "buildtree" / "app" / "output.bin"
        unowned.write_text("artifact\n")

        disabled = self.run_layer("disable", "app")

        self.assertEqual(disabled.returncode, 0, disabled.stdout + disabled.stderr)
        self.assertFalse((self.workspace / "buildtree" / "app" / "src" / "main.c").exists())
        self.assertTrue(unowned.exists())
        self.assertTrue((self.workspace / ".layer" / "cache" / "app" / "src" / "main.c").exists())
        tree = self.tree_json()
        untracked = next(item for item in tree["files"] if item["path"] == "app/output.bin")
        self.assertEqual(untracked["ownership"], "untracked")

    def test_top_wins_masks_lower_layers_and_explain_reports_provenance(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        repo_d = self.make_repo("repo-d", {"common/util.c": "from d\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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

    def test_use_requires_hide_for_layer_that_does_not_provide_path(self) -> None:
        base = self.make_repo("base", {"README.md": "base\n"})
        top = self.make_repo("top", {"common/util.c": "from top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        self.assertTrue((self.workspace / "buildtree" / "common" / "util.c").exists())

        result = self.run_layer("use", "common/util.c", "base")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("common/util.c is not provided by base", result.stderr)
        self.assertIn("layer use common/util.c base --hide", result.stderr)
        self.assertIn("layer adopt common/util.c base", result.stderr)
        self.assertTrue((self.workspace / "buildtree" / "common" / "util.c").exists())
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertNotIn("file_selection", manifest)

    def test_use_hide_assigns_path_to_layer_that_does_not_provide_it(self) -> None:
        base = self.make_repo("base", {"README.md": "base\n"})
        top = self.make_repo("top", {"common/util.c": "from top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        self.assertTrue((self.workspace / "buildtree" / "common" / "util.c").exists())

        result = self.run_layer("use", "common/util.c", "base", "--hide")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("common/util.c assigned to base.", result.stdout)
        self.assertIn("base does not currently provide this file", result.stdout)
        self.assertIn("--hide will hide the path from buildtree", result.stdout)
        self.assertFalse((self.workspace / "buildtree" / "common" / "util.c").exists())
        self.assertTrue((self.workspace / ".layer" / "cache" / "top" / "common" / "util.c").exists())
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["file_selection"]["common/util.c"], {"layer": "base", "hide": True})
        ownership = self.ownership()["common/util.c"]
        self.assertIsNone(ownership["visible"])
        self.assertTrue(ownership["hidden"])
        self.assertEqual(ownership["selected_layer"], "base")
        self.assertEqual(ownership["selected_mount"], "/")
        self.assertEqual(ownership["reason"], "selected layer does not provide this file")
        self.assertEqual([item["layer"] for item in ownership["masked"]], ["top"])
        tree_data = self.tree_json()
        hidden_file = next(item for item in tree_data["files"] if item["path"] == "common/util.c")
        self.assertTrue(hidden_file["hidden"])
        self.assertEqual(hidden_file["selectedLayer"], "base")
        self.assertIsNone(hidden_file["visibleLayer"])
        self.assertEqual(hidden_file["reason"], "selected layer does not provide this file")
        explain = self.run_layer("explain", "common/util.c", "--json")
        data = json.loads(explain.stdout)
        self.assertIsNone(data["visible"])
        self.assertTrue(data["hidden"])
        self.assertEqual(data["selected_layer"], "base")
        self.assertEqual(data["masked"][0]["layer"], "top")
        self.assertEqual(data["reason"], "selected layer does not provide this file")
        self.assertIn("Hidden by selection", self.run_layer("explain", "common/util.c").stdout)
        overlaps = json.loads(self.run_layer("overlaps", "common/util.c", "--json").stdout)
        self.assertIsNone(overlaps["overlaps"][0]["visible"])
        self.assertEqual(overlaps["overlaps"][0]["selected_layer"], "base")
        diff = json.loads(self.run_layer("diff", "common/util.c", "--json").stdout)
        self.assertEqual(diff["hidden"][0]["selected_layer"], "base")
        self.assertEqual(diff["deleted"], [])
        status = self.status_json()
        self.assertEqual(status["buildtree"]["untracked"], [])
        self.assertEqual(status["composed_tree"]["stale_owned_files"], 0)
        adopt_hidden = self.run_layer("adopt", "common/util.c", "base")
        self.assertNotEqual(adopt_hidden.returncode, 0)
        self.assertIn("Cannot adopt common/util.c because it is hidden by selection", adopt_hidden.stderr)
        self.assertIn("layer unuse common/util.c", adopt_hidden.stderr)
        self.assertIn("layer adopt common/util.c base", adopt_hidden.stderr)
        apply_hidden = self.run_layer("apply", "common/util.c")
        self.assertNotEqual(apply_hidden.returncode, 0)
        self.assertIn("Cannot apply common/util.c because it is hidden by selection", apply_hidden.stderr)

        hidden_path = self.workspace / "buildtree" / "common" / "util.c"
        hidden_path.parent.mkdir(parents=True)
        hidden_path.write_text("created in assigned layer\n")
        apply_created = self.run_layer("apply", "common/util.c")
        self.assertEqual(apply_created.returncode, 0, apply_created.stdout + apply_created.stderr)
        self.assertEqual(
            (self.workspace / ".layer" / "cache" / "base" / "common" / "util.c").read_text(),
            "created in assigned layer\n",
        )
        self.assertEqual((self.workspace / ".layer" / "cache" / "top" / "common" / "util.c").read_text(), "from top\n")

        unuse = self.run_layer("unuse", "common/util.c")
        self.assertEqual(unuse.returncode, 0, unuse.stdout + unuse.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from top\n")

    def test_use_hide_does_not_treat_untracked_cache_file_as_adopted(self) -> None:
        base = self.make_repo("base", {"README.md": "base\n"})
        top = self.make_repo("top", {"common/util.c": "from top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        base_file = self.workspace / ".layer" / "cache" / "base" / "common" / "util.c"
        base_file.parent.mkdir(parents=True)
        base_file.write_text("untracked base cache file\n")

        result = self.run_layer("use", "common/util.c", "base", "--hide")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.workspace / "buildtree" / "common" / "util.c").exists())
        ownership = self.ownership()["common/util.c"]
        self.assertIsNone(ownership["visible"])
        self.assertTrue(ownership["hidden"])
        self.assertEqual(ownership["selected_layer"], "base")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["file_selection"]["common/util.c"], {"layer": "base", "hide": True})

    def test_adopt_copies_buildtree_file_into_target_layer_and_stages_new_file(self) -> None:
        base = self.make_repo("base", {"README.md": "base\n"})
        top = self.make_repo("top", {"common/util.c": "from top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("edited in buildtree\n")

        result = self.run_layer("adopt", "common/util.c", "base")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Adopted common/util.c into base.", result.stdout)
        self.assertIn("Staged common/util.c in base.", result.stdout)
        self.assertEqual(
            (self.workspace / ".layer" / "cache" / "base" / "common" / "util.c").read_text(),
            "edited in buildtree\n",
        )
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "edited in buildtree\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["file_selection"]["common/util.c"]["layer"], "base")
        self.assertTrue(manifest["file_selection"]["common/util.c"]["adopted"])
        explain = json.loads(self.run_layer("explain", "common/util.c", "--json").stdout)
        self.assertEqual(explain["visible"]["layer"], "base")
        self.assertEqual(explain["visible"]["sourcePath"], "common/util.c")
        self.assertFalse(explain.get("hidden", False))
        self.assertEqual([item["layer"] for item in explain["masked"]], ["top"])
        git_status = self.run_layer("-L", "base", "git", "status", "--short")
        self.assertEqual(git_status.returncode, 0, git_status.stderr)
        self.assertIn("A  common/util.c", git_status.stdout)

        disabled_top = self.run_layer("disable", "top")
        self.assertEqual(disabled_top.returncode, 0, disabled_top.stdout + disabled_top.stderr)
        disabled_explain = json.loads(self.run_layer("explain", "common/util.c", "--json").stdout)
        self.assertEqual(disabled_explain["visible"]["layer"], "base")
        self.assertFalse(disabled_explain.get("hidden", False))
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "edited in buildtree\n")
        applied = self.run_layer("apply", "common/util.c")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        applied_status = self.run_layer("-L", "base", "git", "status", "--short")
        self.assertEqual(applied_status.returncode, 0, applied_status.stderr)
        self.assertIn("A  common/util.c", applied_status.stdout)

        enabled_top = self.run_layer("enable", "top")
        self.assertEqual(enabled_top.returncode, 0, enabled_top.stdout + enabled_top.stderr)
        enabled_explain = json.loads(self.run_layer("explain", "common/util.c", "--json").stdout)
        self.assertEqual(enabled_explain["visible"]["layer"], "base")
        self.assertFalse(enabled_explain.get("hidden", False))
        self.assertEqual([item["layer"] for item in enabled_explain["masked"]], ["top"])

        selected_top = self.run_layer("use", "common/util.c", "top")
        self.assertEqual(selected_top.returncode, 0, selected_top.stdout + selected_top.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from top\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["file_selection"]["common/util.c"], {"layer": "top"})

        disabled_top_again = self.run_layer("disable", "top")
        self.assertEqual(disabled_top_again.returncode, 0, disabled_top_again.stdout + disabled_top_again.stderr)
        fallback_explain = json.loads(self.run_layer("explain", "common/util.c", "--json").stdout)
        self.assertEqual(fallback_explain["visible"]["layer"], "base")
        self.assertFalse(fallback_explain.get("hidden", False))
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "edited in buildtree\n")

    def test_adopt_no_stage_leaves_new_file_untracked_until_apply(self) -> None:
        base = self.make_repo("base", {"README.md": "base\n"})
        top = self.make_repo("top", {"common/util.c": "from top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("edited in buildtree\n")

        adopted = self.run_layer("adopt", "common/util.c", "base", "--no-stage")
        untracked_status = self.run_layer("-L", "base", "git", "status", "--short")
        applied = self.run_layer("apply", "common/util.c")
        staged_status = self.run_layer("-L", "base", "git", "status", "--short")

        self.assertEqual(adopted.returncode, 0, adopted.stdout + adopted.stderr)
        self.assertIn("Warning: common/util.c is untracked in Git.", adopted.stdout)
        self.assertIn("?? common/", untracked_status.stdout)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertIn("Applied new (staged):", applied.stdout)
        self.assertIn("A  common/util.c", staged_status.stdout)

    def test_adopt_refuses_to_overwrite_different_target_without_force(self) -> None:
        base = self.make_repo("base", {"common/util.c": "from base\n"})
        top = self.make_repo("top", {"common/util.c": "from top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)

        result = self.run_layer("adopt", "common/util.c", "base")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Target layer base already has common/util.c with different content", result.stderr)
        self.assertIn("--force", result.stderr)
        self.assertEqual((self.workspace / ".layer" / "cache" / "base" / "common" / "util.c").read_text(), "from base\n")

    def test_adopt_tracked_target_does_not_stage_by_default_but_can_stage(self) -> None:
        base = self.make_repo("base", {"common/util.c": "from base\n"})
        top = self.make_repo("top", {"common/util.c": "from top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)

        default = self.run_layer("adopt", "common/util.c", "base", "--force")
        default_status = self.run_layer("-L", "base", "git", "status", "--short")
        self.assertEqual(default.returncode, 0, default.stdout + default.stderr)
        self.assertIn("Git did not stage, commit, or push this tracked file.", default.stdout)
        self.assertIn(" M common/util.c", default_status.stdout)

        staged = self.run_layer("adopt", "common/util.c", "base", "--force", "--stage")
        staged_status = self.run_layer("-L", "base", "git", "status", "--short")
        self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
        self.assertIn("Staged common/util.c in base.", staged.stdout)
        self.assertIn("M  common/util.c", staged_status.stdout)

    def test_use_refuses_to_switch_dirty_buildtree_file(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("dirty edit\n")

        result = self.run_layer("use", "common/util.c", "component-b")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("common/util.c has unapplied buildtree edits", result.stderr)
        self.assertIn("layer apply common/util.c", result.stderr)
        self.assertIn("layer adopt common/util.c component-b", result.stderr)
        self.assertIn("layer compose", result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "dirty edit\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertNotIn("file_selection", manifest)

    def test_disable_and_enable_layer_recomposes_without_deleting_cache(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "component-b").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_c), "component-c").returncode, 0)

        disabled = self.run_layer("disable", "component-c")

        self.assertEqual(disabled.returncode, 0, disabled.stdout + disabled.stderr)
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertFalse(manifest["layers"][1]["enabled"])
        self.assertTrue((self.workspace / ".layer" / "cache" / "component-c").exists())
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from b\n")
        status = self.run_layer("status")
        disabled_line = next(line for line in status.stdout.splitlines() if "component-c" in line)
        self.assertIn("git", disabled_line)
        self.assertIn("disabled", disabled_line)

        enabled = self.run_layer("enable", "component-c")

        self.assertEqual(enabled.returncode, 0, enabled.stdout + enabled.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "from c\n")

    def test_move_layer_up_and_down_reorders_composition(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)

        result = self.run_layer("export", str(destination), "--with-provenance")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((destination / "src" / "main.c").exists())
        self.assertTrue((destination / ".layer-provenance.json").exists())
        self.assertTrue((destination / ".layer-lock.yaml").exists())

    def test_export_init_git_creates_standalone_repo_with_commit(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        destination = self.base / "merged-project"
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        untracked = self.workspace / "buildtree" / "build" / "output.bin"
        untracked.parent.mkdir(parents=True)
        untracked.write_text("artifact\n")

        result = self.run_layer("compose")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(untracked.exists())

    def test_compose_preserves_unowned_file_that_collides_with_new_provider(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "layer version\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product", "--no-compose").returncode, 0)
        unowned = self.workspace / "buildtree" / "src" / "main.c"
        unowned.parent.mkdir(parents=True)
        unowned.write_text("local unowned\n")

        result = self.run_layer("compose")
        status = self.run_layer("status", "--json")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("output file exists but is not owned by LayerGit", result.stdout)
        self.assertEqual(unowned.read_text(), "local unowned\n")
        status_data = json.loads(status.stdout)
        self.assertEqual(status_data["composed_tree"]["visible_files"], 0)
        self.assertEqual(status_data["composed_tree"]["untracked_files"], 1)
        self.assertEqual(status_data["conflicts"][0]["kind"], "unowned_output_path")

    def test_compose_clean_can_replace_unowned_file_collision(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "layer version\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product", "--no-compose").returncode, 0)
        unowned = self.workspace / "buildtree" / "src" / "main.c"
        unowned.parent.mkdir(parents=True)
        unowned.write_text("local unowned\n")

        result = self.run_layer("compose", "--clean")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(unowned.read_text(), "layer version\n")

    def test_compose_removes_stale_owned_buildtree_files_by_default(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        untracked = self.workspace / "buildtree" / "build" / "output.bin"
        untracked.parent.mkdir(parents=True)
        untracked.write_text("artifact\n")

        result = self.run_layer("compose", "--clean")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(untracked.exists())
        self.assertTrue((self.workspace / "buildtree" / "src" / "main.c").exists())

    def test_invariant_compose_preserves_unowned_and_reports_it(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        unowned = self.workspace / "buildtree" / "output.bin"
        unowned.write_text("artifact\n")

        result = self.run_layer("compose")
        status = self.status_json()
        tree = self.tree_json()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(unowned.exists())
        self.assertEqual(status["buildtree"]["untracked"], ["output.bin"])
        item = next(item for item in tree["files"] if item["path"] == "output.bin")
        self.assertFalse(item["owned"])
        self.assertEqual(item["ownership"], "untracked")

    def test_invariant_compose_removes_stale_owned_without_making_it_untracked(self) -> None:
        base = self.make_repo("base", {"base.c": "base\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.ownership()["base.c"]["visible"]["layer"], "base")

        disabled = self.run_layer("disable", "base")
        status = self.status_json()
        tree = self.tree_json()

        self.assertEqual(disabled.returncode, 0, disabled.stdout + disabled.stderr)
        self.assertFalse((self.workspace / "buildtree" / "base.c").exists())
        self.assertEqual(status["buildtree"]["untracked"], [])
        self.assertEqual(status["composed_tree"]["visible_files"], 0)
        self.assertNotIn("base.c", {item["path"] for item in tree["files"] if item["ownership"] == "untracked"})

    def test_invariant_ownership_metadata_matches_visible_buildtree_files(self) -> None:
        base = self.make_repo("base", {"common/util.c": "base\n", "base.c": "base\n"})
        top = self.make_repo("top", {"common/util.c": "top\n", "top.c": "top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)

        self.assert_current_ownership_matches_buildtree()
        ownership = self.ownership()
        self.assertEqual(ownership["common/util.c"]["visible"]["layer"], "top")
        self.assertEqual([item["layer"] for item in ownership["common/util.c"]["masked"]], ["base"])

    def test_invariant_top_layer_wins_is_deterministic_across_recompose(self) -> None:
        base = self.make_repo("base", {"common/util.c": "base\n"})
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)

        for _ in range(3):
            result = self.run_layer("compose")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "top\n")
            ownership = self.ownership()["common/util.c"]
            self.assertEqual(ownership["visible"]["layer"], "top")
            self.assertEqual([item["layer"] for item in ownership["masked"]], ["base"])

    def test_invariant_layer_use_changes_only_selected_path(self) -> None:
        base = self.make_repo("base", {"common/util.c": "base util\n", "common/other.c": "base other\n"})
        top = self.make_repo("top", {"common/util.c": "top util\n", "common/other.c": "top other\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)

        result = self.run_layer("use", "common/util.c", "base")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "base util\n")
        self.assertEqual((self.workspace / "buildtree" / "common" / "other.c").read_text(), "top other\n")
        ownership = self.ownership()
        self.assertEqual(ownership["common/util.c"]["visible"]["layer"], "base")
        self.assertEqual(ownership["common/other.c"]["visible"]["layer"], "top")

    def test_invariant_disabling_higher_layer_unmasks_lower_layer(self) -> None:
        base = self.make_repo("base", {"common/util.c": "base\n"})
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "top\n")

        disabled = self.run_layer("disable", "top")

        self.assertEqual(disabled.returncode, 0, disabled.stdout + disabled.stderr)
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "base\n")
        self.assertEqual(self.ownership()["common/util.c"]["visible"]["layer"], "base")
        self.assert_current_ownership_matches_buildtree(disabled_layers={"top"})

    def test_invariant_apply_updates_visible_owner_only(self) -> None:
        base = self.make_repo("base", {"common/util.c": "base\n"})
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("edited\n")

        result = self.run_layer("apply", "common/util.c")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / ".layer" / "cache" / "top" / "common" / "util.c").read_text(), "edited\n")
        self.assertEqual((self.workspace / ".layer" / "cache" / "base" / "common" / "util.c").read_text(), "base\n")
        base_status = self.run_layer("-L", "base", "git", "status", "--short")
        top_status = self.run_layer("-L", "top", "git", "status", "--short")
        self.assertEqual(base_status.stdout, "")
        self.assertIn("M common/util.c", top_status.stdout)

    def test_apply_tracked_file_with_stage_stages_it(self) -> None:
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("edited\n")

        result = self.run_layer("apply", "common/util.c", "--stage")
        git_status = self.run_layer("-L", "top", "git", "status", "--short")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Applied modified (staged):", result.stdout)
        self.assertIn("M  common/util.c", git_status.stdout)

    def test_invariant_apply_new_writes_only_write_layer_and_stages(self) -> None:
        product = self.make_repo("product", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        new_file = self.workspace / "buildtree" / "newfile.c"
        new_file.write_text("new\n")

        result = self.run_layer("apply", "--new")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / ".layer" / "cache" / "workspace-base" / "newfile.c").read_text(), "new\n")
        self.assertFalse((self.workspace / ".layer" / "cache" / "product" / "newfile.c").exists())
        write_status = self.run_layer("-L", "workspace-base", "git", "status", "--short")
        product_status = self.run_layer("-L", "product", "git", "status", "--short")
        self.assertIn("A  newfile.c", write_status.stdout)
        self.assertEqual(product_status.stdout, "")

    def test_apply_new_with_no_stage_leaves_file_untracked_and_warns(self) -> None:
        self.assertEqual(self.run_layer("init").returncode, 0)
        new_file = self.workspace / "buildtree" / "newfile.c"
        new_file.write_text("new\n")

        result = self.run_layer("apply", "--new", "--no-stage")
        git_status = self.run_layer("-L", "workspace-base", "git", "status", "--short")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Applied new (not staged):", result.stdout)
        self.assertIn("Warning: new files were copied but left untracked in Git.", result.stdout)
        self.assertIn("?? newfile.c", git_status.stdout)

    def test_apply_all_stages_new_but_not_tracked_modifications_by_default(self) -> None:
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("edited\n")
        (self.workspace / "buildtree" / "newfile.c").write_text("new\n")

        result = self.run_layer("apply", "--all")
        top_status = self.run_layer("-L", "top", "git", "status", "--short")
        base_status = self.run_layer("-L", "workspace-base", "git", "status", "--short")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Applied modified (not staged):", result.stdout)
        self.assertIn("Applied new (staged):", result.stdout)
        self.assertIn(" M common/util.c", top_status.stdout)
        self.assertIn("A  newfile.c", base_status.stdout)

    def test_apply_all_no_stage_stages_nothing(self) -> None:
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("edited\n")
        (self.workspace / "buildtree" / "newfile.c").write_text("new\n")

        result = self.run_layer("apply", "--all", "--no-stage")
        top_status = self.run_layer("-L", "top", "git", "status", "--short")
        base_status = self.run_layer("-L", "workspace-base", "git", "status", "--short")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Applied modified (not staged):", result.stdout)
        self.assertIn("Applied new (not staged):", result.stdout)
        self.assertIn(" M common/util.c", top_status.stdout)
        self.assertIn("?? newfile.c", base_status.stdout)

    def test_apply_all_stage_stages_tracked_modifications_and_new_files(self) -> None:
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("edited\n")
        (self.workspace / "buildtree" / "newfile.c").write_text("new\n")

        result = self.run_layer("apply", "--all", "--stage")
        top_status = self.run_layer("-L", "top", "git", "status", "--short")
        base_status = self.run_layer("-L", "workspace-base", "git", "status", "--short")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Applied modified (staged):", result.stdout)
        self.assertIn("Applied new (staged):", result.stdout)
        self.assertIn("M  common/util.c", top_status.stdout)
        self.assertIn("A  newfile.c", base_status.stdout)

    def test_invariant_apply_rejects_path_traversal_without_writing_outside_cache(self) -> None:
        product = self.make_repo("product", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        outside = self.base / "outside.txt"

        for bad_path in ("../outside.txt", str(outside), "subdir/../../outside.txt"):
            result = self.run_layer("apply", bad_path)
            self.assertNotEqual(result.returncode, 0, bad_path)

        self.assertFalse(outside.exists())
        self.assertFalse((self.workspace / ".layer" / "cache" / "workspace-base" / "outside.txt").exists())
        self.assertFalse((self.workspace / ".layer" / "cache" / "product" / "outside.txt").exists())

    def test_invariant_compose_clean_is_explicitly_destructive_only_for_output_tree(self) -> None:
        product = self.make_repo("product", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        unowned = self.workspace / "buildtree" / "output.bin"
        unowned.write_text("artifact\n")

        normal = self.run_layer("compose")
        self.assertEqual(normal.returncode, 0, normal.stdout + normal.stderr)
        self.assertTrue(unowned.exists())

        clean = self.run_layer("compose", "--clean")

        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertFalse(unowned.exists())
        self.assertEqual((self.workspace / "buildtree" / "src" / "main.c").read_text(), "ok\n")
        self.assertTrue((self.workspace / ".layer" / "cache" / "product" / "src" / "main.c").exists())

    def test_invariant_stale_ownership_metadata_is_not_current_valid_state(self) -> None:
        product = self.make_repo("product", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        manifest_data = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        manifest_data["layers"] = []
        (self.workspace / "layer.yaml").write_text(yaml.safe_dump(manifest_data, sort_keys=False))

        status = self.status_json()
        tree = self.tree_json()
        explain = self.run_layer("explain", "src/main.c", "--json")

        self.assertEqual(status["composed_tree"]["visible_files"], 0)
        self.assertEqual(status["composed_tree"]["masked_files"], 0)
        self.assertEqual(status["composed_tree"]["stale_owned_files"], 1)
        self.assertEqual(status["composed_tree"]["untracked_files"], 0)
        self.assertEqual(tree["files"][0]["ownership"], "stale")
        explain_data = json.loads(explain.stdout)
        self.assertTrue(explain_data["stale_owned"])
        self.assertIn("previously owned by LayerGit", explain_data["reason"])

    def test_extension_json_list_tree_and_explain_are_valid(self) -> None:
        repo_b = self.make_repo("repo-b", {"common/util.c": "from b\n"})
        repo_c = self.make_repo("repo-c", {"common/util.c": "from c\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        cached_file = self.workspace / ".layer" / "cache" / "product" / "src" / "main.c"
        cached_file.write_text("after\n")

        result = self.run_layer("pull", "--no-fetch")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "buildtree" / "src" / "main.c").read_text(), "after\n")

    def test_diff_reports_modified_owned_buildtree_file(self) -> None:
        product = self.make_repo("repo-a", {"common/util.c": "before\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "component-b").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("after\n")

        plain = self.run_layer("diff")
        json_result = self.run_layer("diff", "common/util.c", "--json")

        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn("Modified:", plain.stdout)
        self.assertIn("common/util.c -> component-b", plain.stdout)
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        data = json.loads(json_result.stdout)
        self.assertEqual(data["modified"][0]["path"], "common/util.c")
        self.assertEqual(data["modified"][0]["layer"], "component-b")
        self.assertEqual(data["new"], [])
        self.assertEqual(data["deleted"], [])

    def test_apply_owned_buildtree_change_copies_to_layer_cache(self) -> None:
        product = self.make_repo("repo-a", {"common/util.c": "before\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "component-b").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("after\n")

        result = self.run_layer("apply", "common/util.c")
        git_status = self.run_layer("-L", "component-b", "git", "status", "--short")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            (self.workspace / ".layer" / "cache" / "component-b" / "common" / "util.c").read_text(),
            "after\n",
        )
        self.assertIn("M common/util.c", git_status.stdout)

    def test_apply_new_unowned_file_uses_write_layer(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        new_file = self.workspace / "buildtree" / "generated" / "config.h"
        new_file.parent.mkdir(parents=True)
        new_file.write_text("#define X 1\n")

        diff = self.run_layer("diff", "--new", "--json")
        result = self.run_layer("apply", "--new")
        git_status = self.run_layer("-L", "workspace-base", "git", "status", "--short")

        self.assertEqual(diff.returncode, 0, diff.stderr)
        data = json.loads(diff.stdout)
        self.assertEqual(data["new"][0]["path"], "generated/config.h")
        self.assertEqual(data["new"][0]["write_layer"], "workspace-base")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            (self.workspace / ".layer" / "cache" / "workspace-base" / "generated" / "config.h").read_text(),
            "#define X 1\n",
        )
        self.assertIn("A  generated/config.h", git_status.stdout)

    def test_apply_new_file_initializes_local_layer_cache_before_staging(self) -> None:
        self.assertEqual(self.run_layer("init").returncode, 0)
        local_git = self.workspace / ".layer" / "cache" / "workspace-base" / ".git"
        shutil.rmtree(local_git)
        new_file = self.workspace / "buildtree" / ".gitignore"
        new_file.write_text("local\n")

        result = self.run_layer("apply", ".gitignore")
        git_status = self.run_layer("-L", "workspace-base", "git", "status", "--short")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(local_git.exists())
        self.assertEqual((self.workspace / ".layer" / "cache" / "workspace-base" / ".gitignore").read_text(), "local\n")
        self.assertIn("A  .gitignore", git_status.stdout)

    def test_apply_new_file_requires_write_layer(self) -> None:
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        new_file = self.workspace / "buildtree" / "generated" / "config.h"
        new_file.parent.mkdir(parents=True)
        new_file.write_text("#define X 1\n")

        result = self.run_layer("apply", "generated/config.h")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("generated/config.h is not owned by any layer and no write layer is configured", result.stderr)
        self.assertIn("layer add --local local-edits", result.stderr)

    def test_apply_deleted_owned_file_requires_delete_flag(self) -> None:
        product = self.make_repo("repo-a", {"old/file.c": "old\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "component-b").returncode, 0)
        (self.workspace / "buildtree" / "old" / "file.c").unlink()

        diff = self.run_layer("diff")
        skipped = self.run_layer("apply", "old/file.c")

        self.assertEqual(diff.returncode, 0, diff.stderr)
        self.assertIn("Deleted:", diff.stdout)
        self.assertIn("old/file.c -> component-b", diff.stdout)
        self.assertEqual(skipped.returncode, 0, skipped.stdout + skipped.stderr)
        self.assertTrue((self.workspace / ".layer" / "cache" / "component-b" / "old" / "file.c").exists())
        self.assertIn("Deleted buildtree file not applied", skipped.stdout)
        deleted = self.run_layer("apply", "--delete", "old/file.c")
        self.assertEqual(deleted.returncode, 0, deleted.stdout + deleted.stderr)
        self.assertFalse((self.workspace / ".layer" / "cache" / "component-b" / "old" / "file.c").exists())

    def test_cli_validation_edges_for_init_add_apply_and_git(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})

        missing_manifest = self.run_layer("add")
        self.assertNotEqual(missing_manifest.returncode, 0)
        self.assertIn("No layer.yaml", missing_manifest.stderr)

        self.assertEqual(self.run_layer("init").returncode, 0)
        self.assertNotEqual(self.run_layer("init").returncode, 0)
        self.assertIn("layer.yaml already exists", self.run_layer("init").stderr)
        self.assertIn("add requires a repo path", self.run_layer("add").stderr)
        self.assertIn("Use `layer add --local <name>`", self.run_layer("add", "--local").stderr)
        self.assertIn("Use `layer add --local <name>`", self.run_layer("add", "--local", "a", "b").stderr)
        self.assertIn("--revision is only valid", self.run_layer("add", "--local", "local-rev", "--revision", "main").stderr)

        first = self.run_layer("add", str(product), "product", "--no-compose")
        duplicate = self.run_layer("add", str(product), "product")
        revision = self.run_layer("add", str(product), "product-rev", "--revision", "HEAD", "--no-compose")
        inferred_duplicate = self.run_layer("add", str(product), "--no-compose")
        no_args_apply = self.run_layer("apply")
        missing_git_args = self.run_layer("-L", "product", "git")

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("Layer `product` already exists", duplicate.stderr)
        self.assertEqual(revision.returncode, 0, revision.stdout + revision.stderr)
        self.assertIn("repo-a", inferred_duplicate.stdout)
        self.assertIn("apply requires a path", no_args_apply.stderr)
        self.assertIn("Missing git command", missing_git_args.stderr)

    def test_cli_remove_list_tree_compose_and_pull_edges(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "ok\n"})
        other = self.make_repo("repo-b", {"README.md": "readme\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(product), "product").returncode, 0)
        self.assertEqual(self.run_layer("add", str(other), "other").returncode, 0)

        list_plain = self.run_layer("list")
        tree_plain = self.run_layer("tree")
        compose_json = self.run_layer("compose", "--json")
        pull_checked = self.run_layer("pull", "product", "--no-compose")
        remove_many = self.run_layer("remove", "all")
        git_many = self.run_layer("-L", "all", "git", "status")
        explain_plain = self.run_layer("explain", "src/main.c")
        removed = self.run_layer("remove", "other", "--delete-cache")

        self.assertEqual(list_plain.returncode, 0, list_plain.stderr)
        self.assertIn("product", list_plain.stdout)
        self.assertEqual(tree_plain.returncode, 0, tree_plain.stderr)
        self.assertIn("src/main.c -> product", tree_plain.stdout)
        self.assertEqual(compose_json.returncode, 0, compose_json.stderr)
        self.assertIn("visible_files", json.loads(compose_json.stdout))
        self.assertEqual(pull_checked.returncode, 0, pull_checked.stdout + pull_checked.stderr)
        self.assertIn("Pulled 1 layer", pull_checked.stdout)
        self.assertIn("remove expects exactly one layer", remove_many.stderr)
        self.assertIn("git requires exactly one layer", git_many.stderr)
        self.assertIn("Visible file:", explain_plain.stdout)
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertFalse((self.workspace / ".layer" / "cache" / "other").exists())

    def test_cli_move_use_unuse_write_and_help_edges(self) -> None:
        repo_a = self.make_repo("repo-a", {"common/util.c": "a\n"})
        repo_b = self.make_repo("repo-b", {"common/util.c": "b\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_a), "a").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "b").returncode, 0)

        self.assertEqual(self.run_layer("move", "a", "up").returncode, 0)
        self.assertEqual(self.run_layer("move", "a", "down").returncode, 0)
        self.assertEqual(self.run_layer("move", "a", "before", "b").returncode, 0)
        self.assertEqual(self.run_layer("move", "a", "after", "b").returncode, 0)
        self.assertIn("move before requires", self.run_layer("move", "a", "before").stderr)
        self.assertIn("move after requires", self.run_layer("move", "a", "after").stderr)
        self.assertIn("layer movement expects", self.run_layer("move", "all", "top").stderr)
        self.assertIn("Unknown layer", self.run_layer("use", "common/util.c", "missing").stderr)

        self.assertEqual(self.run_layer("disable", "b").returncode, 0)
        self.assertIn("disabled", self.run_layer("use", "common/util.c", "b").stderr)
        self.assertEqual(self.run_layer("enable", "b").returncode, 0)
        self.assertIn("enable/disable expects", self.run_layer("disable", "all").stderr)
        self.assertIn("write expects", self.run_layer("write", "all").stderr)
        self.assertIn("No explicit file selection", self.run_layer("unuse", "none.c").stdout)
        self.assertEqual(self.run_layer("use", "common/util.c", "a").returncode, 0)
        manifest_data = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        manifest_data.setdefault("file_precedence", {})["common/util.c"] = {"order": ["b"]}
        (self.workspace / "layer.yaml").write_text(yaml.safe_dump(manifest_data, sort_keys=False))
        self.assertEqual(self.run_layer("unuse", "common/util.c").returncode, 0)
        self.assertNotIn("file_precedence", yaml.safe_load((self.workspace / "layer.yaml").read_text()))
        self.assertEqual(self.run_layer("help").returncode, 0)
        self.assertIn("not a layer command", self.run_layer("help", "missing").stderr)

    def test_cli_conflicts_warnings_merge_and_diff_no_changes(self) -> None:
        repo_a = self.make_repo("repo-a", {"common/util.c": "a\n", "a/dup.c": "a\n"})
        repo_b = self.make_repo("repo-b", {"common/util.c": "b\n", "b/dup.c": "b\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_a), "a").returncode, 0)
        self.assertEqual(self.run_layer("add", str(repo_b), "b").returncode, 0)

        no_diff = self.run_layer("diff")
        no_apply = self.run_layer("apply", "--all")
        self.assertIn("No buildtree changes.", no_diff.stdout)
        self.assertIn("No buildtree changes to apply.", no_apply.stdout)

        manifest_data = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        manifest_data["conflicts"]["forbid_duplicate_basenames"] = ["**/*.c"]
        (self.workspace / "layer.yaml").write_text(yaml.safe_dump(manifest_data, sort_keys=False))
        warning = self.run_layer("compose")
        self.assertEqual(warning.returncode, 0, warning.stdout + warning.stderr)
        self.assertIn("WARNING:", warning.stdout)

        manifest_data = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        manifest_data["composition"]["same_path_policy"] = "error"
        (self.workspace / "layer.yaml").write_text(yaml.safe_dump(manifest_data, sort_keys=False))
        conflict = self.run_layer("compose")
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("ERROR:", conflict.stdout)

        manifest_data["composition"]["same_path_policy"] = "top_wins"
        manifest_data["conflicts"].pop("forbid_duplicate_basenames", None)
        (self.workspace / "layer.yaml").write_text(yaml.safe_dump(manifest_data, sort_keys=False))
        self.assertEqual(self.run_layer("compose").returncode, 0)
        merged = self.run_layer("merge", "1..2", "--name", "merged", "--with-provenance", "--init-git")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertTrue((self.workspace / ".layer" / "cache" / "merged" / ".git").exists())

    def test_layer_scoped_git_uses_global_layer_selector(self) -> None:
        product = self.make_repo("repo-a", {"src/main.c": "before\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
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
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)

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
        self.assertIn("diff", result.stdout)
        self.assertIn("apply", result.stdout)
        self.assertIn("add <repo> [name]", result.stdout)
        self.assertIn("add --local <name>", result.stdout)
        self.assertIn("move <layer> <pos>", result.stdout)
        self.assertIn("write <layer>", result.stdout)
        self.assertIn("layer init --output ./buildtree --no-base-layer", result.stdout)
        self.assertIn("layer add --local local-edits", result.stdout)
        self.assertIn("layer move repoa up", result.stdout)
        self.assertIn("compose --clean", result.stdout)
        self.assertIn("layer compose --clean", result.stdout)
        self.assertIn("layer diff common/util.c", result.stdout)
        self.assertIn("layer apply common/util.c", result.stdout)
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
        self.assertIn("--short", result.stdout)

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
        self.assertIn("layergit.addLayer", commands)
        self.assertIn("layergit.addLocalLayer", commands)
        self.assertIn("layergit.compose", commands)
        self.assertIn("layergit.openOutput", commands)
        self.assertIn("layergit.setWriteLayer", commands)
        self.assertIn("layergit.openLayerCache", commands)
        self.assertIn("layergit.gitStatusLayer", commands)
        self.assertIn("layergit.applyLayer", commands)
        self.assertIn("layergit.applyAll", commands)
        self.assertIn("layergit.clearSelection", commands)
        self.assertIn("layergit.explainCurrentFile", commands)
        self.assertIn("layergit.moveLayerUp", commands)
        self.assertIn("layergit.moveLayerDown", commands)
        self.assertIn("layergit.sendLayerToTop", commands)
        self.assertIn("layergit.sendLayerToBottom", commands)
        self.assertIn("layergit.useLayerForFile", commands)
        self.assertIn("layergit.applyFile", commands)
        title_menus = package["contributes"]["menus"]["view/title"]
        add_menu = next(item for item in title_menus if item["command"] == "layergit.addLayer")
        init_menu = next(item for item in title_menus if item["command"] == "layergit.init")
        self.assertIn("layergit.workspaceFound", add_menu["when"])
        self.assertIn("!layergit.workspaceFound", init_menu["when"])
        item_menus = package["contributes"]["menus"]["view/item/context"]
        apply_layer_menu = next(item for item in item_menus if item["command"] == "layergit.applyLayer")
        self.assertIn("viewItem =~ /layergit\\.layer\\./", apply_layer_menu["when"])
        self.assertTrue((ROOT / "vscode-extension" / "src" / "cli.ts").exists())
        self.assertTrue((ROOT / "vscode-extension" / "src" / "layersView.ts").exists())
        self.assertTrue((ROOT / "vscode-extension" / "src" / "composedTreeView.ts").exists())


if __name__ == "__main__":
    unittest.main()
