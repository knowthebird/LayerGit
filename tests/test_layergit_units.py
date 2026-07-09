from __future__ import annotations

import json
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from layergit import cli, composer, exporter, gitops, manifest, merger, reports, selectors, worktree
from layergit.errors import LayerError


class LayerGitUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_selectors_cover_supported_forms_and_errors(self) -> None:
        layers = [
            {"name": "base", "enabled": True},
            {"name": "mid", "enabled": False},
            {"name": "top", "enabled": True},
        ]

        self.assertEqual(selectors.select_layers([], "all"), [])
        self.assertEqual(selectors.select_layers(layers, None), [0, 1, 2])
        self.assertEqual(selectors.select_layers(layers, "all", enabled_only_for_default=True), [0, 2])
        self.assertEqual(selectors.select_layers(layers, "all-layers"), [0, 1, 2])
        self.assertEqual(selectors.select_layers(layers, "enabled"), [0, 2])
        self.assertEqual(selectors.select_layers(layers, "disabled"), [1])
        self.assertEqual(selectors.select_layers(layers, "top"), [2])
        self.assertEqual(selectors.select_layers(layers, "top", enabled_only_for_default=True), [2])
        self.assertEqual(selectors.select_layers(layers, "1,top,1"), [0, 2])
        self.assertEqual(selectors.select_layers(layers, "1..2"), [0, 1])
        self.assertEqual(selectors.select_layers(layers, "2"), [1])
        self.assertEqual(selectors.select_layers(layers, "mid"), [1])
        self.assertEqual(selectors.insertion_index(layers, before=None, after=None, top=True), 3)
        self.assertEqual(selectors.insertion_index(layers, before="mid", after=None, top=False), 1)
        self.assertEqual(selectors.insertion_index(layers, before=None, after="mid", top=False), 2)

        with self.assertRaisesRegex(LayerError, "Invalid selector range"):
            selectors.select_layers(layers, "2..1")
        with self.assertRaisesRegex(LayerError, "Expected a layer index"):
            selectors.one_based_index(layers, "base")
        with self.assertRaisesRegex(LayerError, "Layer index out of range"):
            selectors.select_layers(layers, "4")
        with self.assertRaisesRegex(LayerError, "No layer matches"):
            selectors.select_layers(layers, "missing")
        with self.assertRaisesRegex(LayerError, "Use only one"):
            selectors.insertion_index(layers, before="base", after="top", top=False)

    def test_gitops_covers_error_and_status_branches(self) -> None:
        missing = self.root / "missing"
        self.assertIsNone(gitops.current_commit(missing))
        self.assertIsNone(gitops.current_branch(missing))
        self.assertEqual(gitops.porcelain_status(missing), "missing")
        self.assertEqual(gitops.tracked_files(missing), [])

        with patch("layergit.gitops.run_git", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(LayerError, "git executable"):
                gitops.is_git_repo(self.root)

        with patch("layergit.gitops.run_git") as run_git:
            run_git.return_value = subprocess.CompletedProcess([], 1, "", "")
            self.assertFalse(gitops.is_git_repo(self.root))

        with patch("layergit.gitops.run_git") as run_git, patch.object(Path, "resolve", side_effect=OSError):
            run_git.return_value = subprocess.CompletedProcess([], 0, f"{self.root}\n", "")
            self.assertFalse(gitops.is_git_repo(self.root))

        with patch("layergit.gitops.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess([], 0, f"{self.root}\n", ""),
                subprocess.CompletedProcess([], 1, "", ""),
            ]
            self.assertIsNone(gitops.current_commit(self.root))

        with patch("layergit.gitops.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess([], 0, f"{self.root}\n", ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
            self.assertEqual(gitops.current_branch(self.root), "detached")

        with patch("layergit.gitops.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess([], 0, f"{self.root}\n", ""),
                subprocess.CompletedProcess([], 1, "", ""),
            ]
            self.assertIsNone(gitops.current_branch(self.root))

        with patch("layergit.gitops.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess([], 0, f"{self.root}\n", ""),
                subprocess.CompletedProcess([], 1, "", ""),
            ]
            self.assertEqual(gitops.porcelain_status(self.root), "error")

        with patch("layergit.gitops.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess([], 0, f"{self.root}\n", ""),
                subprocess.CompletedProcess([], 0, " M file.c\n", ""),
            ]
            self.assertEqual(gitops.porcelain_status(self.root), "modified")

        with patch("layergit.gitops.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess([], 0, f"{self.root}\n", ""),
                subprocess.CompletedProcess([], 1, "", ""),
            ]
            self.assertEqual(gitops.tracked_files(self.root), [])

        with patch("layergit.gitops.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess([], 0, f"{self.root}\n", ""),
                subprocess.CompletedProcess([], 0, "abc123\n", ""),
            ]
            self.assertEqual(gitops.current_commit(self.root), "abc123")

    def test_sync_layer_branches_are_covered_with_mocks(self) -> None:
        layer = {"name": "repo", "repo": "/src", "revision": "main"}
        target = gitops.layer_cache_path(self.root, "repo")
        target.mkdir(parents=True)

        with patch("layergit.gitops.is_git_repo", return_value=True), patch("layergit.gitops.run_git") as run_git:
            run_git.return_value = subprocess.CompletedProcess([], 0, "", "")
            gitops.sync_layer(self.root, layer)
            self.assertEqual(run_git.call_args_list[0].args[0], ["fetch", "--all", "--prune"])

        with patch("layergit.gitops.is_git_repo", return_value=True), patch("layergit.gitops.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 1, "", "fatal\n"),
            ]
            with self.assertRaisesRegex(LayerError, "fatal"):
                gitops.sync_layer(self.root, layer)

        with patch("layergit.gitops.is_git_repo", return_value=False), patch("layergit.gitops.run_git") as run_git:
            gitops.ensure_local_layer_repo(self.root, "local")
            run_git.assert_called_with(["init"], gitops.layer_cache_path(self.root, "local"))

        with patch("layergit.gitops.ensure_local_layer_repo") as ensure:
            gitops.sync_layer(self.root, {"name": "local", "kind": "local"})
            ensure.assert_called_once_with(self.root, "local")

        with self.assertRaisesRegex(LayerError, "has no repo path"):
            gitops.sync_layer(self.root, {"name": "no-repo"})

        with patch("layergit.gitops.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "clone failed")
            with self.assertRaisesRegex(LayerError, "clone failed"):
                gitops.sync_layer(self.root, {"name": "new", "repo": "/src"})

        with patch("layergit.gitops.subprocess.run") as run, patch("layergit.gitops.run_git") as run_git:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            gitops.sync_layer(self.root, {"name": "new-rev", "repo": "/src", "revision": "v1"})
            run_git.assert_called_once_with(["checkout", "v1"], gitops.layer_cache_path(self.root, "new-rev"))

        removable = gitops.layer_cache_path(self.root, "remove-me")
        removable.mkdir(parents=True)
        gitops.remove_cache(self.root, "remove-me")
        self.assertFalse(removable.exists())

    def test_composer_helper_branches(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "keep.c").write_text("keep\n")
        (source / "skip.c").write_text("skip\n")
        (source / "nested").mkdir()
        with patch("layergit.composer.tracked_files", return_value=["keep.c", "skip.c", "nested"]):
            files = composer.iter_layer_files(source, {"include": ["*.c"], "exclude": ["skip.c"]})
        self.assertEqual(files, [("keep.c", source / "keep.c")])
        with patch("layergit.composer.tracked_files", return_value=["keep.c"]):
            self.assertEqual(composer.iter_layer_files(source, {"include": ["*.h"]}), [])
        self.assertEqual(composer.matching_pattern("src/a.c", ("src/**",)), "src/**")

        cached_source = gitops.layer_cache_path(self.root, "source")
        cached_source.mkdir(parents=True)
        (cached_source / "keep.c").write_text("keep\n")
        (cached_source / "skip.c").write_text("skip\n")
        with patch("layergit.composer.tracked_files", return_value=["missing.c", "keep.c", "skip.c"]):
            providers = composer.file_providers(
                self.root,
                {"layers": [{"name": "source", "include": ["*.c"], "exclude": ["skip.c"]}]},
                "keep.c",
            )
        self.assertEqual(providers, ["source"])
        with patch("layergit.composer.tracked_files", return_value=["keep.c"]):
            self.assertEqual(
                composer.file_providers(
                    self.root,
                    {"layers": [{"name": "source", "enabled": False}]},
                    "keep.c",
                ),
                [],
            )
        with patch("layergit.composer.tracked_files", return_value=[]):
            self.assertEqual(
                composer.file_providers(self.root, {"layers": [{"name": "source"}]}, "missing.c"),
                [],
            )
        with patch("layergit.composer.tracked_files", return_value=["directory"]):
            (cached_source / "directory").mkdir()
            self.assertEqual(
                composer.file_providers(self.root, {"layers": [{"name": "source"}]}, "directory"),
                [],
            )
        with patch("layergit.composer.tracked_files", return_value=["keep.c"]):
            self.assertEqual(
                composer.file_providers(self.root, {"layers": [{"name": "source", "include": ["*.h"]}]}, "keep.c"),
                [],
            )
        with patch("layergit.composer.tracked_files", return_value=["skip.c"]):
            self.assertEqual(
                composer.file_providers(self.root, {"layers": [{"name": "source", "exclude": ["skip.c"]}]}, "skip.c"),
                [],
            )

        provider_a = composer.Provider(0, "a", None, "1", source, "common/util.c", source / "keep.c", ())
        provider_b = composer.Provider(1, "b", None, "2", source, "common/util.c", source / "skip.c", ())
        conflicts: list[dict] = []
        warnings: list[dict] = []
        ordered, reason, has_precedence = composer.order_providers_for_path(
            "common/util.c",
            [provider_a, provider_b],
            {"file_precedence": {"common/util.c": {"order": ["b"]}}, "layers": [{"name": "a"}, {"name": "b"}]},
            conflicts,
            warnings,
        )
        self.assertEqual([item.layer_name for item in ordered], ["a", "b"])
        self.assertTrue(has_precedence)
        self.assertIn("file-specific", reason)

        conflicts = []
        warnings = []
        composer.order_providers_for_path(
            "common/util.c",
            [provider_a],
            {
                "file_precedence": {"common/util.c": {"order": ["disabled", "missing"]}},
                "layers": [{"name": "disabled", "enabled": False}, {"name": "a"}],
            },
            conflicts,
            warnings,
        )
        self.assertEqual(warnings[0]["kind"], "disabled_file_precedence")
        self.assertEqual(conflicts[0]["kind"], "invalid_file_precedence")

        self.assertTrue(composer.override_allowed("src/a.c", provider_a, ("src/**",)))
        self.assertEqual(composer.matching_pattern("src/a.c", ("lib/**",)), "<unknown>")
        self.assertTrue(composer.path_matches("src/a.c", "src/**"))
        self.assertFalse(composer.path_matches("src/a.c", "lib/**"))

        conflict, warning = composer.duplicate_basename_findings(
            {"a/foo.c": provider_a, "b/foo.c": provider_b, "hidden": None},
            {"conflicts": {"forbid_duplicate_basenames": ["**/*.c"], "duplicate_basename_policy": "warn"}},
        )
        self.assertEqual(conflict, [])
        self.assertEqual(warning[0]["kind"], "duplicate_basename")
        conflict, warning = composer.duplicate_basename_findings(
            {"a/foo.c": provider_a, "b/foo.c": provider_b},
            {"conflicts": {"forbid_duplicate_basenames": ["**/*.c"], "duplicate_basename_policy": "error"}},
        )
        self.assertEqual(conflict[0]["kind"], "duplicate_basename")
        self.assertEqual(warning, [])

    def test_write_output_tree_clean_and_stale_paths(self) -> None:
        output = self.root / "out"
        source = self.root / "source.txt"
        source.write_text("source\n")
        stale = output / "stale" / "old.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text("old\n")
        extra = output / "extra.txt"
        extra.write_text("extra\n")
        provider = composer.Provider(0, "a", None, None, self.root, "visible.txt", source, ())

        with patch("layergit.composer.sync_layer") as sync_layer:
            with self.assertRaisesRegex(LayerError, "Cache for layer"):
                composer.compose(self.root, {"layers": [{"name": "missing"}]})
            sync_layer.assert_called_once()

        composer.write_output_tree(
            output,
            {"visible.txt": provider},
            {"stale/old.txt": {"visible": {"layer": "a"}}},
            clean=False,
        )
        self.assertFalse(stale.exists())
        self.assertTrue((output / "visible.txt").exists())

        composer.write_output_tree(output, {"visible.txt": provider}, {}, clean=True)
        self.assertFalse(extra.exists())

        blocked = output / "blocked"
        blocked.mkdir()
        (blocked / "child.txt").write_text("child\n")
        composer.remove_empty_parents(blocked, output)
        self.assertTrue(blocked.exists())

    def test_reports_formatting_and_explain_edge_cases(self) -> None:
        status = {
            "layers": [{"index": 1, "name": "local", "kind": "local", "enabled": True, "status": "clean", "branch": "main", "commit": "abc", "top": True}],
            "write_layer": "local",
            "composed_tree": {"output": "./out", "visible_files": 0, "masked_files": 0, "conflicts": 0, "warnings": 0},
            "conflicts": [{"path": "a.c", "providers": [{"layer": "a"}, {"layer": "b"}]}],
            "warnings": [{"path": "b.c", "providers": [{"layer": "a"}]}],
            "modified_files": [{"path": "c.c", "layer": "a"}],
            "buildtree": {"untracked": ["new.c"], "stale_owned": ["stale.c"]},
        }
        formatted = reports.format_status(status)
        self.assertIn("LayerGit status", formatted)
        self.assertIn("Write:     local", formatted)
        self.assertIn("write-layer, top, bottom", formatted)
        self.assertIn("stale.c", formatted)
        short = reports.format_status_short(status)
        self.assertIn("Write layer: local", short)
        no_layers = {**status, "layers": [], "write_layer": None}
        no_layers_text = reports.format_status(no_layers)
        self.assertIn("<none>", no_layers_text)
        self.assertIn("Create or select a local layer", no_layers_text)
        no_layers_short = reports.format_status_short(no_layers)
        self.assertIn("<none>", no_layers_short)
        self.assertIn("Create or select a local layer", no_layers_short)
        self.assertEqual(reports.status_git_label("modified"), "dirty")
        self.assertEqual(reports.overlap_reason("custom policy"), "custom policy")
        self.assertEqual(reports.overlap_reason(None), "top-layer-wins")
        stale_overlaps = reports.format_overlaps({"stale": True, "overlaps": []})
        self.assertIn("WARNING: ownership metadata may be stale", stale_overlaps)
        with patch("layergit.reports.current_ownership", return_value={"hiddenless.c": {"visible": None, "masked": [{"layer": "base"}]}}), patch(
            "layergit.reports.workspace_status",
            return_value={"composed_tree": {"stale_owned_files": 0}},
        ):
            hiddenless_overlap = reports.overlap_report(self.root, {"layers": [{"name": "base"}]})
        self.assertEqual(hiddenless_overlap["overlaps"], [])

        self.assertEqual(reports.layer_position(2, [1, 2, 3]), None)
        self.assertEqual(reports.iter_output_files(self.root / "missing"), [])
        self.assertEqual(reports.current_ownership_entry({"visible": {"layer": "x"}}, set()), None)
        self.assertEqual(reports.current_ownership_entry({"visible": {"layer": "x"}}, {"y"}), None)
        self.assertEqual(
            reports.current_ownership_entry(
                {"visible": None, "hidden": True, "selected_layer": "x", "masked": [{"layer": "x"}]},
                {"x"},
            )["masked"],
            [{"layer": "x"}],
        )
        self.assertEqual(reports.current_findings([{"providers": []}], {"x"}), [])
        self.assertEqual(reports.current_findings([{"providers": [{"layer": "x"}]}], {"x"}), [{"providers": [{"layer": "x"}]}])

        out = self.root / "out"
        out.mkdir()
        (out / "changed.c").write_text("changed\n")
        cache = gitops.layer_cache_path(self.root, "a")
        cache.mkdir(parents=True)
        (cache / "changed.c").write_text("original\n")
        self.assertEqual(
            reports.modified_output_files(
                self.root,
                {"workspace": {"output": "./out"}},
                {"skip.c": {"visible": None}, "changed.c": {"visible": {"layer": "a", "source_path": "changed.c"}}},
            ),
            [{"path": "changed.c", "layer": "a"}],
        )

        layer = {"name": "disabled", "enabled": False}
        cache = gitops.layer_cache_path(self.root, "disabled")
        cache.mkdir(parents=True)
        with patch("layergit.composer.tracked_files", return_value=["ghost.c"]):
            (cache / "ghost.c").write_text("ghost\n")
            entry = reports.explain_file(self.root, "ghost.c", {"workspace": {"output": "./out"}, "layers": [layer]})
        self.assertEqual(entry["disabled_providers"][0]["layer"], "disabled")
        self.assertIn("Disabled providers", reports.format_explain("ghost.c", entry))
        with patch("layergit.composer.tracked_files", return_value=["ghost.c"]):
            disabled_json = reports.explain_json(
                self.root,
                "ghost.c",
                {"workspace": {"output": "./out"}, "layers": [{"name": "disabled", "enabled": False}]},
            )
        self.assertEqual(disabled_json["disabled_providers"][0]["sourcePath"], "ghost.c")
        self.assertEqual(reports.explain_file(self.root, "none.c", {"workspace": {"output": "./out"}, "layers": []}), None)
        self.assertEqual(reports.explain_json(self.root, "none.c", {"workspace": {"output": "./out"}, "layers": []}), None)
        self.assertIn("No ownership record", reports.format_explain("none.c", None))
        self.assertIn(
            "stale owned file",
            reports.format_explain(
                "stale.c",
                {"unowned": True, "stale_owned": True, "visible": None, "masked": [], "reason": "stale"},
            ),
        )
        self.assertIn(
            "untracked buildtree file",
            reports.format_explain(
                "new.c",
                {"unowned": True, "untracked": True, "visible": None, "masked": [], "reason": "new"},
            ),
        )
        self.assertIn(
            "Hidden by selection",
            reports.format_explain(
                "hidden.c",
                {
                    "visible": None,
                    "selected_layer": "base",
                    "hidden": True,
                    "masked": [{"layer": "top", "source_path": "hidden.c"}],
                    "reason": "hidden",
                },
            ),
        )
        none_visible = reports.format_explain(
            "selected.c",
            {
                "visible": None,
                "selected_layer": "base",
                "masked": [{"layer": "lower", "source_path": "selected.c"}],
                "reason": "selected provider is unavailable",
            },
        )
        self.assertIn("Selected layer:", none_visible)
        self.assertIn("Masked providers:", none_visible)
        self.assertIn(
            "Masked lower-layer files",
            reports.format_explain(
                "visible.c",
                {
                    "visible": {"layer": "top", "repo": None, "commit": "abc", "source_path": "visible.c"},
                    "masked": [{"layer": "base", "source_path": "visible.c"}],
                    "reason": "top wins",
                },
            ),
        )
        reports.add_extension_fields({"layer": "a", "source_path": "a.c", "commit": "abc"}, {"a": 1})

    def test_doctor_report_helper_edges(self) -> None:
        output = self.root / "out"
        output.mkdir()
        status = {
            "composed_tree": {"output": "./out", "stale_owned_files": 0, "untracked_files": 0},
        }
        cache = gitops.layer_cache_path(self.root, "layer-a")
        cache.mkdir(parents=True)
        manifest_data = {
            "workspace": {"output": "./out", "write_layer": "missing"},
            "layers": [{"name": "layer-a", "kind": "git", "enabled": True}],
        }
        with patch("layergit.reports.workspace_status", return_value=status), patch(
            "layergit.reports.is_git_repo", side_effect=LayerError("git failed")
        ), patch("layergit.reports.overlap_report", return_value={"overlaps": []}):
            failed = reports.doctor_report(self.root, manifest_data)
        self.assertTrue(any(item["id"] == "workspace.write_layer" and item["level"] == "error" for item in failed["checks"]))
        self.assertTrue(any(item["id"] == "layer.cache_git" for item in failed["checks"]))

        with patch("layergit.reports.workspace_status", return_value=status), patch(
            "layergit.reports.is_git_repo", return_value=False
        ), patch("layergit.reports.overlap_report", return_value={"overlaps": []}):
            not_repo = reports.doctor_report(self.root, {**manifest_data, "workspace": {"output": "./out"}})
        self.assertTrue(any(item["message"].endswith("is not a Git repo") for item in not_repo["checks"]))

        with patch("layergit.reports.workspace_status", return_value=status), patch(
            "layergit.reports.is_git_repo", return_value=True
        ), patch("layergit.reports.git_porcelain", return_value=["A  staged.c", "?? new.c"]), patch(
            "layergit.reports.current_commit", return_value="abc"
        ), patch("layergit.reports.unpushed_commit_count", return_value=2), patch(
            "layergit.reports.overlap_report", return_value={"overlaps": [{"path": "a.c"}]}
        ):
            dirty = reports.doctor_report(self.root, {**manifest_data, "workspace": {"output": "./out", "write_layer": "layer-a"}})
        self.assertTrue(any(item["id"] == "layer.staged" for item in dirty["checks"]))
        self.assertTrue(any(item["id"] == "layer.untracked" for item in dirty["checks"]))
        self.assertTrue(any(item["id"] == "sharing.unpushed" for item in dirty["checks"]))
        self.assertTrue(any(item["id"] == "overlaps.summary" and item["level"] == "warning" for item in dirty["checks"]))
        formatted = reports.format_doctor_report(dirty)
        self.assertIn("run `layer overlaps`", formatted)
        self.assertIn("Result: errors found", reports.format_doctor_report({"status": "error", "checks": [], "summary": {"errors": 1}}))
        self.assertIn(
            "Result: ok",
            reports.format_doctor_report(
                {
                    "status": "ok",
                    "checks": [{"id": "custom.check", "level": "ok", "message": "custom ok"}],
                    "summary": {"ok": 1, "warnings": 0, "errors": 0},
                }
            ),
        )

        with patch("layergit.reports.run_git", return_value=subprocess.CompletedProcess(["git"], 1, "", "")):
            self.assertEqual(reports.git_porcelain(cache), [])
            self.assertEqual(reports.unpushed_commit_count(cache), 0)
        with patch("layergit.reports.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess(["git"], 0, "origin/main\n", ""),
                subprocess.CompletedProcess(["git"], 1, "", ""),
            ]
            self.assertEqual(reports.unpushed_commit_count(cache), 0)
        with patch("layergit.reports.run_git") as run_git:
            run_git.side_effect = [
                subprocess.CompletedProcess(["git"], 0, "origin/main\n", ""),
                subprocess.CompletedProcess(["git"], 0, "not-a-number\n", ""),
            ]
            self.assertEqual(reports.unpushed_commit_count(cache), 0)

    def test_worktree_edge_cases_and_dry_run(self) -> None:
        output = self.root / "out"
        output.mkdir()
        with self.assertRaisesRegex(LayerError, "Invalid buildtree path"):
            worktree.normalize_buildtree_path("../bad", self.root, output)
        with self.assertRaisesRegex(LayerError, "not inside"):
            worktree.normalize_buildtree_path(str(self.root / "other.txt"), self.root, output)
        self.assertEqual(worktree.normalize_buildtree_path("out/a.c", self.root, output), "a.c")
        self.assertEqual(worktree.display_path(self.root, Path("/tmp/outside-layergit-test")), "/tmp/outside-layergit-test")

        source = output / "new.c"
        source.write_text("new\n")
        item = {
            "path": "new.c",
            "write_layer": "local",
            "buildtree_path": "out/new.c",
            "layer_path": ".layer/cache/local/new.c",
        }
        result = worktree.apply_buildtree_changes(
            self.root,
            {"workspace": {"write_layer": "local"}},
            {"modified": [], "new": [item], "deleted": []},
            dry_run=True,
        )
        self.assertEqual(result["new"], [item])
        self.assertFalse((self.root / ".layer" / "cache" / "local" / "new.c").exists())
        hidden_result = worktree.apply_buildtree_changes(
            self.root,
            {"workspace": {"write_layer": "local"}},
            {"modified": [], "new": [], "deleted": [], "hidden": [{"path": "hidden.c", "selected_layer": "mask"}]},
            dry_run=True,
        )
        self.assertEqual(hidden_result["skipped_hidden"], [{"path": "hidden.c", "selected_layer": "mask"}])
        worktree.stage_layer_file(self.root, {}, {"path": "missing.c"}, dry_run=False)
        with patch("layergit.worktree.is_git_repo", return_value=True), patch("layergit.worktree.run_git") as run_git:
            run_git.return_value = subprocess.CompletedProcess(["git"], 1, "", "fatal: no pathspec\n")
            with self.assertRaisesRegex(LayerError, "staging failed"):
                worktree.stage_layer_file(
                    self.root,
                    {"layers": [{"name": "gitlayer", "kind": "git"}]},
                    {"path": "file.c", "layer": "gitlayer", "source_path": "file.c"},
                    dry_run=False,
                )
        with patch("layergit.worktree.is_git_repo", return_value=False):
            with self.assertRaisesRegex(LayerError, "not a Git repository"):
                worktree.stage_layer_file(
                    self.root,
                    {"layers": [{"name": "gitlayer", "kind": "git"}]},
                    {"path": "file.c", "layer": "gitlayer", "source_path": "file.c"},
                    dry_run=False,
                )
        with self.assertRaisesRegex(LayerError, "ownership metadata is stale"):
            worktree.delete_from_layer(self.root, {"layers": []}, {"path": "bad.c", "layer": "owner"}, dry_run=False, stage=True)
        with self.assertRaisesRegex(LayerError, "ownership metadata is stale"):
            worktree.delete_from_layer(
                self.root,
                {"layers": []},
                {"path": "bad.c", "layer": "owner", "source_path": "../bad.c"},
                dry_run=False,
                stage=True,
            )
        with patch("layergit.worktree.is_git_repo", return_value=False):
            with self.assertRaisesRegex(LayerError, "not a Git repository"):
                worktree.delete_from_layer(
                    self.root,
                    {"layers": [{"name": "owner", "kind": "git"}]},
                    {"path": "bad.c", "layer": "owner", "source_path": "bad.c"},
                    dry_run=False,
                    stage=True,
                )
        with patch("layergit.worktree.is_git_repo", return_value=True):
            with self.assertRaisesRegex(LayerError, "ownership metadata is stale"):
                worktree.delete_from_layer(
                    self.root,
                    {"layers": [{"name": "owner", "kind": "git"}]},
                    {"path": "missing.c", "layer": "owner", "source_path": "missing.c"},
                    dry_run=False,
                    stage=True,
                )
        delete_cache = gitops.layer_cache_path(self.root, "owner")
        delete_cache.mkdir(parents=True, exist_ok=True)
        (delete_cache / "dry.c").write_text("dry\n")
        with patch("layergit.worktree.is_git_repo", return_value=True):
            worktree.delete_from_layer(
                self.root,
                {"layers": [{"name": "owner", "kind": "git"}]},
                {"path": "dry.c", "layer": "owner", "source_path": "dry.c"},
                dry_run=True,
                stage=True,
            )
        self.assertTrue((delete_cache / "dry.c").exists())
        local_cache = gitops.layer_cache_path(self.root, "local")
        local_cache.mkdir(parents=True)
        (local_cache / "local.c").write_text("local\n")
        with patch("layergit.worktree.ensure_local_layer_repo", return_value=local_cache):
            worktree.delete_from_layer(
                self.root,
                {"layers": [{"name": "local", "kind": "local"}]},
                {"path": "local.c", "layer": "local", "source_path": "local.c"},
                dry_run=True,
                stage=True,
            )
        with patch("layergit.worktree.is_git_repo", return_value=True), patch.object(Path, "relative_to", side_effect=ValueError):
            with self.assertRaisesRegex(LayerError, "ownership metadata is stale"):
                worktree.delete_from_layer(
                    self.root,
                    {"layers": [{"name": "owner", "kind": "git"}]},
                    {"path": "dry.c", "layer": "owner", "source_path": "dry.c"},
                    dry_run=True,
                    stage=True,
                )
        with patch("layergit.worktree.is_git_repo", return_value=True), patch("layergit.worktree.run_git") as run_git:
            run_git.return_value = subprocess.CompletedProcess(["git"], 1, "", "fatal: no pathspec\n")
            with self.assertRaisesRegex(LayerError, "Deleted fail.c from layer owner, but staging failed"):
                (delete_cache / "fail.c").write_text("fail\n")
                worktree.delete_from_layer(
                    self.root,
                    {"layers": [{"name": "owner", "kind": "git"}]},
                    {"path": "fail.c", "layer": "owner", "source_path": "fail.c"},
                    dry_run=False,
                    stage=True,
                )

        ownership_file = self.root / ".layer" / "ownership.json"
        ownership_file.parent.mkdir(parents=True, exist_ok=True)
        ownership_file.write_text(
            json.dumps(
                {
                    "skip-hidden.c": {"visible": None},
                    "skip-owner.c": {"visible": {"source_path": "skip-owner.c"}},
                    "owned.c": {"visible": {"layer": "owner", "source_path": "owned.c"}},
                    "stale.c": {"visible": {"layer": "old", "source_path": "stale.c"}},
                }
            )
        )
        owner_cache = gitops.layer_cache_path(self.root, "owner")
        owner_cache.mkdir(parents=True, exist_ok=True)
        (owner_cache / "owned.c").write_text("old\n")
        (output / "owned.c").write_text("new\n")
        (output / "skip-hidden.c").write_text("hidden\n")
        (output / "skip-owner.c").write_text("ownerless\n")
        (output / "stale.c").write_text("stale\n")
        (output / "untracked.c").write_text("new\n")
        diff = worktree.buildtree_diff(
            self.root,
            {"workspace": {"output": "./out", "write_layer": "local"}, "layers": [{"name": "owner"}]},
            path="missing.c",
        )
        self.assertEqual(diff["modified"], [])
        diff = worktree.buildtree_diff(
            self.root,
            {"workspace": {"output": "./out", "write_layer": "local"}, "layers": [{"name": "owner"}]},
            layer="other",
        )
        self.assertEqual(diff["modified"], [])
        self.assertEqual(diff["new"], [])
        diff = worktree.buildtree_diff(
            self.root,
            {"workspace": {"output": "./out", "write_layer": "local"}, "layers": [{"name": "owner"}]},
        )
        self.assertEqual(diff["modified"][0]["path"], "owned.c")
        self.assertIn("stale.c", {item["path"] for item in diff["stale"]})
        with patch(
            "layergit.worktree.current_ownership",
            return_value={
                "hidden.c": {"visible": None},
                "ownerless.c": {"visible": {"source_path": "ownerless.c"}},
            },
        ):
            malformed = worktree.buildtree_diff(
                self.root,
                {"workspace": {"output": "./out"}, "layers": [{"name": "owner"}]},
            )
        self.assertEqual(malformed["modified"], [])
        blocked = gitops.layer_cache_path(self.root, "owner") / "blocked"
        blocked.mkdir()
        (blocked / "child.txt").write_text("child\n")
        worktree.remove_empty_parents(blocked, gitops.layer_cache_path(self.root, "owner"))
        self.assertTrue(blocked.exists())

    def test_composer_adopted_provider_edge_cases(self) -> None:
        self.assertEqual(
            composer.iter_adopted_file_layers(
                {"file_selection": {"bad.c": {"adopted": True, "layer": 123}, "skip.c": {"layer": "base"}}}
            ),
            [],
        )
        enabled_layers = [
            (1, {"name": "base", "mount": "/"}),
            (2, {"name": "app", "mount": "/app"}),
            (3, {"name": "missing", "mount": "/"}),
            (4, {"name": "excluded", "mount": "/", "exclude": ["excluded.c"]}),
        ]
        manifest_data = {
            "file_selection": {
                "disabled.c": {"layer": "disabled", "adopted": True},
                "docs/readme.md": {"layer": "app", "adopted": True},
                "missing.c": {"layer": "missing", "adopted": True},
                "excluded.c": {"layer": "excluded", "adopted": True},
                "adopted.c": {"layer": "base", "adopted": True},
            }
        }
        (gitops.layer_cache_path(self.root, "excluded") / "excluded.c").parent.mkdir(parents=True)
        (gitops.layer_cache_path(self.root, "excluded") / "excluded.c").write_text("excluded\n")
        (gitops.layer_cache_path(self.root, "base")).mkdir(parents=True)
        (gitops.layer_cache_path(self.root, "base") / "adopted.c").write_text("adopted\n")
        providers: dict[str, list[composer.Provider]] = {}

        composer.add_adopted_cache_file_providers(self.root, manifest_data, enabled_layers, providers)

        self.assertEqual(list(providers), ["adopted.c"])
        self.assertEqual(composer.file_providers(self.root, {"layers": [{"name": "app", "mount": "/app"}]}, "docs/readme.md"), [])
        output = self.root / "out"
        output.mkdir()
        (output / "changed.c").write_text("old\n")
        source_root = gitops.layer_cache_path(self.root, "base")
        (source_root / "changed.c").write_text("new\n")
        provider = composer.Provider(
            layer_index=1,
            layer_name="base",
            repo=None,
            commit="new",
            source_root=source_root,
            source_path="changed.c",
            abs_path=source_root / "changed.c",
            overrides=(),
        )
        dirty = composer.dirty_owned_output_files(
            self.root,
            {"workspace": {"output": "./out"}},
            {"changed.c": {"visible": {"layer": "base", "source_path": "changed.c", "commit": "old"}}},
            {"changed.c": provider},
        )
        self.assertEqual(dirty, [])

    def test_merger_and_exporter_branches(self) -> None:
        with self.assertRaisesRegex(LayerError, "No layers selected"):
            merger.merge_layers(self.root, {"layers": []}, [], "merged")
        with self.assertRaisesRegex(LayerError, "already exists"):
            merger.merge_layers(self.root, {"layers": [{"name": "merged"}]}, [0], "merged")

        manifest_data = {"layers": [{"name": "a"}, {"name": "b"}], "conflicts": {"duplicate_basename_policy": "warn"}}
        merge_tmp = self.root / ".layer" / "merge-tmp" / "merged"
        merge_tmp.mkdir(parents=True)
        (merge_tmp / "file.c").write_text("merged\n")
        (self.root / ".layer").mkdir(exist_ok=True)
        (self.root / ".layer" / "ownership.json").write_text("{}\n")
        existing_target = gitops.layer_cache_path(self.root, "merged")
        existing_target.mkdir(parents=True)
        (existing_target / "old.c").write_text("old\n")
        with patch("layergit.merger.compose", return_value={"conflicts": []}), patch(
            "layergit.merger.init_repo_with_commit"
        ) as init_repo:
            result = merger.merge_layers(self.root, manifest_data, [0, 1], "merged", init_git=True, with_provenance=True)
        self.assertEqual(result["layers"][0]["name"], "merged")
        self.assertTrue((gitops.layer_cache_path(self.root, "merged") / ".layer-provenance.json").exists())
        init_repo.assert_called_once()

        with patch("layergit.merger.compose", return_value={"conflicts": [{"path": "x"}]}):
            with self.assertRaisesRegex(LayerError, "conflicts"):
                merger.merge_layers(self.root, {"layers": [{"name": "a"}]}, [0], "out")

        out = self.root / "out"
        out.mkdir()
        (out / "file.c").write_text("file\n")
        (self.root / "layer.lock.yaml").write_text("layers: []\n")
        destination = self.root / "export"
        destination.mkdir()
        (destination / "old.c").write_text("old\n")
        with patch("layergit.exporter.compose", return_value={"conflicts": []}), patch(
            "layergit.exporter.init_repo_with_commit"
        ) as init_repo:
            exporter.export_workspace(
                self.root,
                {"workspace": {"output": "./out"}},
                destination,
                init_git=True,
                with_provenance=True,
            )
        self.assertTrue((destination / "file.c").exists())
        self.assertTrue((destination / ".layer-lock.yaml").exists())
        init_repo.assert_called_once()

        with patch("layergit.exporter.compose", return_value={"conflicts": [{"path": "x"}]}):
            with self.assertRaisesRegex(LayerError, "conflicts"):
                exporter.export_workspace(self.root, {"workspace": {"output": "./out"}}, self.root / "bad")

    def test_manifest_and_cli_helpers(self) -> None:
        with self.assertRaisesRegex(LayerError, "No layer.yaml"):
            manifest.load_manifest(self.root)
        self.assertEqual(manifest.normalize_mount(None), "/")
        self.assertEqual(manifest.normalize_mount(""), "/")
        self.assertEqual(manifest.normalize_mount("."), "/")
        self.assertEqual(manifest.normalize_mount("///"), "/")
        self.assertEqual(manifest.normalize_mount("app//src"), "/app/src")
        self.assertEqual(manifest.buildtree_path_for_source("src/main.c", "/app"), "app/src/main.c")
        self.assertEqual(manifest.source_path_for_buildtree("app/src/main.c", "/app"), "src/main.c")
        self.assertIsNone(manifest.source_path_for_buildtree("app", "/app"))
        self.assertIsNone(manifest.source_path_for_buildtree("docs/readme.md", "/app"))
        for bad_mount in ("../outside", "/tmp/outside", "C:\\temp", "app\\src", "app/../../outside"):
            with self.assertRaisesRegex(LayerError, "Invalid layer mount"):
                manifest.normalize_mount(bad_mount)
        self.assertIsNone(cli.gitignore_output_entry(self.root, "/outside"))
        self.assertIsNone(cli.gitignore_output_entry(self.root, "."))
        self.assertEqual(cli.display_path(self.root, Path("/tmp/outside-layergit-cli-test")), "/tmp/outside-layergit-cli-test")
        self.assertEqual(cli.infer_layer_name("git@example.com:Team/My Repo.git", []), "my-repo")
        self.assertEqual(cli.unique_layer_name("layer", [{"name": "layer"}, {"name": "layer-2"}]), "layer-3")
        self.assertEqual(cli.invalid_command(["--version"]), None)
        self.assertEqual(cli.invalid_command(["-L", "a", "status"]), None)
        self.assertEqual(cli.invalid_command(["-L", "a"]), None)
        self.assertEqual(cli.infer_layer_name("git@example.com:repo.git", []), "repo")
        existing_gitignore = self.root / ".gitignore"
        existing_gitignore.write_text("# BEGIN LayerGit\nold\n# END LayerGit\n")
        cli.ensure_gitignore(self.root, "./out")
        self.assertIn("/out/", existing_gitignore.read_text())
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.cmd_help("layer", []), 0)
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.cmd_help("layer", ["help"]), 1)

    def test_cli_apply_delete_validation_edges(self) -> None:
        self.assertFalse(cli.deprecated_apply_delete_order(["status"]))
        self.assertTrue(cli.deprecated_apply_delete_order(["apply", "--delete", "--to", "base", "ghost.c"]))
        self.assertFalse(cli.deprecated_apply_delete_order(["apply", "ghost.c", "--delete"]))
        apply_to_missing_path = SimpleNamespace(
            path=None,
            all=False,
            new=False,
            target_layer=None,
            to_layer="base",
            delete=False,
        )
        with self.assertRaisesRegex(LayerError, "apply --to requires a path"):
            cli.cmd_apply(self.root, apply_to_missing_path)
        apply_to_combined = SimpleNamespace(
            path="ghost.c",
            all=True,
            new=False,
            target_layer=None,
            to_layer="base",
            delete=False,
        )
        with self.assertRaisesRegex(LayerError, "cannot be combined"):
            cli.cmd_apply(self.root, apply_to_combined)
        args = SimpleNamespace(
            path="ghost.c",
            all=False,
            new=False,
            target_layer=None,
            to_layer=None,
            delete=True,
            dry_run=False,
            stage=False,
            no_stage=False,
        )
        with patch("layergit.cli.load_manifest", return_value={"workspace": {"output": "./out"}}), patch(
            "layergit.cli.buildtree_diff",
            return_value={"modified": [], "new": [], "deleted": [], "hidden": []},
        ), patch("layergit.cli.explain_file", return_value={"hidden": True}):
            with self.assertRaisesRegex(LayerError, "hidden by selection"):
                cli.cmd_apply(self.root, args)
        with patch("layergit.cli.load_manifest", return_value={"workspace": {"output": "./out"}}), patch(
            "layergit.cli.buildtree_diff",
            return_value={"modified": [], "new": [], "deleted": [], "hidden": []},
        ), patch("layergit.cli.explain_file", return_value={"unowned": True}):
            with self.assertRaisesRegex(LayerError, "not owned by LayerGit"):
                cli.cmd_apply(self.root, args)
        with patch("layergit.cli.load_manifest", return_value={"workspace": {"output": "./out"}}), patch(
            "layergit.cli.buildtree_diff",
            return_value={"modified": [], "new": [], "deleted": [], "hidden": []},
        ), patch("layergit.cli.explain_file", return_value={"visible": {"layer": "base"}}):
            with self.assertRaisesRegex(LayerError, "ownership metadata is stale"):
                cli.cmd_apply(self.root, args)

    def test_cli_formatter_and_cmd_add_helper_edges(self) -> None:
        manifest_data = {
            "workspace": {"output": "./out"},
            "composition": {"same_path_policy": "top_wins"},
            "layers": [{"name": "repo-a", "repo": "/tmp/repo-a", "enabled": True}],
            "conflicts": {"duplicate_basename_policy": "warn"},
        }
        manifest.save_manifest(self.root, manifest_data)
        args = cli.argparse.Namespace(
            local=False,
            repo="/tmp/repo-a",
            name=None,
            revision=None,
            mount="/",
            before=None,
            after=None,
            top=False,
            no_sync=True,
            no_compose=True,
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.cmd_add(self.root, args), 0)
        self.assertIn("repo-a-2", stdout.getvalue())

        diff_text = cli.format_buildtree_diff(
            {
                "modified": [{"path": "a.c", "layer": "base"}],
                "new": [{"path": "b.c", "write_layer": "local"}],
                "deleted": [],
                "stale": [],
            }
        )
        self.assertIn("\n\nNew:", diff_text)
        self.assertIn("b.c -> write layer local", diff_text)

        apply_text = cli.format_apply_result(
            {
                "modified": [{"path": "a.c", "layer": "base"}],
                "new": [{"path": "b.c", "write_layer": "local"}],
                "deleted": [],
                "skipped_deleted": [],
            },
            dry_run=True,
        )
        self.assertIn("\n\nWould apply new:", apply_text)


if __name__ == "__main__":
    unittest.main()
