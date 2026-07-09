from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class LayerGitInvariantTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.fixtures = self.base / "fixtures"
        self.fixtures.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_layer(self, *args: str) -> subprocess.CompletedProcess[str]:
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
            cwd=self.workspace,
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

    def git_status(self, layer: str) -> str:
        result = self.run_layer("-L", layer, "git", "status", "--short")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_invariant_compose_disable_remove_hide_and_clean_do_not_delete_source_files(self) -> None:
        base = self.make_repo("base", {"common/util.c": "base\n", "keep/base.c": "base keep\n"})
        top = self.make_repo("top", {"common/util.c": "top\n", "keep/top.c": "top keep\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)

        self.assertEqual(self.run_layer("compose").returncode, 0)
        self.assertEqual(self.run_layer("disable", "top").returncode, 0)
        self.assertEqual(self.run_layer("enable", "top").returncode, 0)
        self.assertEqual(self.run_layer("use", "common/util.c", "base", "--hide").returncode, 0)
        self.assertEqual(self.run_layer("compose", "--clean").returncode, 0)
        self.assertEqual(self.run_layer("remove", "top").returncode, 0)

        self.assertEqual((self.workspace / ".layer" / "cache" / "base" / "common" / "util.c").read_text(), "base\n")
        self.assertEqual((self.workspace / ".layer" / "cache" / "base" / "keep" / "base.c").read_text(), "base keep\n")
        self.assertEqual((self.workspace / ".layer" / "cache" / "top" / "common" / "util.c").read_text(), "top\n")
        self.assertEqual((self.workspace / ".layer" / "cache" / "top" / "keep" / "top.c").read_text(), "top keep\n")

    def test_invariant_apply_delete_preserves_masked_lower_layer_and_stages_owner_only(self) -> None:
        base = self.make_repo("base", {"common/util.c": "base\n"})
        top = self.make_repo("top", {"common/util.c": "top\n", "other.c": "top other\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        (self.workspace / ".layer" / "cache" / "top" / "other.c").write_text("dirty but unrelated\n")
        (self.workspace / "buildtree" / "common" / "util.c").unlink()

        deleted = self.run_layer("apply", "common/util.c", "--delete")

        self.assertEqual(deleted.returncode, 0, deleted.stdout + deleted.stderr)
        self.assertFalse((self.workspace / ".layer" / "cache" / "top" / "common" / "util.c").exists())
        self.assertEqual((self.workspace / ".layer" / "cache" / "base" / "common" / "util.c").read_text(), "base\n")
        self.assertEqual(self.git_status("base"), "")
        top_status = self.git_status("top")
        self.assertIn("D  common/util.c", top_status)
        self.assertIn(" M other.c", top_status)

    def test_invariant_apply_to_layer_writes_only_target_and_updates_selection(self) -> None:
        base = self.make_repo("base", {"README.md": "base\n"})
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        (self.workspace / "buildtree" / "common" / "util.c").write_text("chosen content\n")

        applied = self.run_layer("apply", "common/util.c", "--to", "base")

        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual((self.workspace / ".layer" / "cache" / "base" / "common" / "util.c").read_text(), "chosen content\n")
        self.assertEqual((self.workspace / ".layer" / "cache" / "top" / "common" / "util.c").read_text(), "top\n")
        manifest = yaml.safe_load((self.workspace / "layer.yaml").read_text())
        self.assertEqual(manifest["file_selection"]["common/util.c"]["layer"], "base")
        self.assertIn("A  common/util.c", self.git_status("base"))
        self.assertEqual(self.git_status("top"), "")

    def test_invariant_dry_run_commands_do_not_modify_sources_metadata_or_index(self) -> None:
        base = self.make_repo("base", {"common/util.c": "base\n"})
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        ownership_before = (self.workspace / ".layer" / "ownership.json").read_text()
        manifest_before = (self.workspace / "layer.yaml").read_text()
        base_before = (self.workspace / ".layer" / "cache" / "base" / "common" / "util.c").read_text()
        top_before = (self.workspace / ".layer" / "cache" / "top" / "common" / "util.c").read_text()

        self.assertEqual(self.run_layer("add", "--local", "mask").returncode, 0)
        ownership_before = (self.workspace / ".layer" / "ownership.json").read_text()
        manifest_before = (self.workspace / "layer.yaml").read_text()
        self.assertEqual(self.run_layer("use", "common/util.c", "mask", "--hide", "--dry-run").returncode, 0)

        (self.workspace / "buildtree" / "common" / "util.c").write_text("dry-run edit\n")
        new_file = self.workspace / "buildtree" / "new.c"
        new_file.write_text("new\n")

        self.assertEqual(self.run_layer("apply", "common/util.c", "--dry-run").returncode, 0)
        self.assertEqual(self.run_layer("apply", "new.c", "--to", "base", "--dry-run").returncode, 0)
        self.assertEqual(self.run_layer("compose", "--dry-run").returncode, 0)

        self.assertEqual((self.workspace / ".layer" / "ownership.json").read_text(), ownership_before)
        self.assertEqual((self.workspace / "layer.yaml").read_text(), manifest_before)
        self.assertEqual((self.workspace / ".layer" / "cache" / "base" / "common" / "util.c").read_text(), base_before)
        self.assertEqual((self.workspace / ".layer" / "cache" / "top" / "common" / "util.c").read_text(), top_before)
        self.assertEqual(self.git_status("base"), "")
        self.assertEqual(self.git_status("top"), "")
        self.assertEqual((self.workspace / "buildtree" / "common" / "util.c").read_text(), "dry-run edit\n")
        self.assertEqual(new_file.read_text(), "new\n")

        (self.workspace / "buildtree" / "common" / "util.c").unlink()
        self.assertEqual(self.run_layer("apply", "common/util.c", "--delete", "--dry-run").returncode, 0)
        self.assertEqual((self.workspace / ".layer" / "cache" / "top" / "common" / "util.c").read_text(), top_before)
        self.assertEqual((self.workspace / ".layer" / "ownership.json").read_text(), ownership_before)
        self.assertEqual(self.git_status("top"), "")

    def test_invariant_json_commands_have_extension_contract_fields_without_tracebacks(self) -> None:
        base = self.make_repo("base", {"README.md": "base\n"})
        top = self.make_repo("top", {"common/util.c": "top\n"})
        self.assertEqual(self.run_layer("init", "--no-base-layer").returncode, 0)
        self.assertEqual(self.run_layer("add", str(base), "base").returncode, 0)
        self.assertEqual(self.run_layer("add", str(top), "top").returncode, 0)
        self.assertEqual(self.run_layer("use", "common/util.c", "base", "--hide").returncode, 0)

        for args in (
            ("status", "--json"),
            ("tree", "--json"),
            ("explain", "common/util.c", "--json"),
            ("doctor", "--json"),
        ):
            result = self.run_layer(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Traceback", result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertIsInstance(data, dict)

        status = json.loads(self.run_layer("status", "--json").stdout)
        tree = json.loads(self.run_layer("tree", "--json").stdout)
        explain = json.loads(self.run_layer("explain", "common/util.c", "--json").stdout)
        doctor = json.loads(self.run_layer("doctor", "--json").stdout)
        self.assertIn("layers", status)
        self.assertIn("composed_tree", status)
        hidden_entries = [
            item
            for item in tree["files"]
            if item["path"] == "common/util.c" and item.get("hidden")
        ]
        self.assertTrue(hidden_entries)
        self.assertTrue(explain["hidden"])
        self.assertEqual(explain["selected_layer"], "base")
        self.assertIn("status", doctor)
        self.assertIn("checks", doctor)
        self.assertIn("summary", doctor)
