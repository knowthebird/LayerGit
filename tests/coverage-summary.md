# LayerGit Coverage Summary

Generated with:

```bash
coverage erase
LAYERGIT_TEST_COVERAGE=1 coverage run --parallel-mode -m unittest
coverage combine
coverage report --format=markdown
```

| Name                     |    Stmts |     Miss |    Cover |   Missing |
|------------------------- | -------: | -------: | -------: | --------: |
| layergit/\_\_init\_\_.py |        1 |        0 |     100% |           |
| layergit/cli.py          |      555 |        0 |     100% |           |
| layergit/composer.py     |      214 |        0 |     100% |           |
| layergit/errors.py       |        1 |        0 |     100% |           |
| layergit/exporter.py     |       23 |        0 |     100% |           |
| layergit/gitops.py       |       87 |        0 |     100% |           |
| layergit/manifest.py     |       50 |        0 |     100% |           |
| layergit/merger.py       |       34 |        0 |     100% |           |
| layergit/reports.py      |      232 |        0 |     100% |           |
| layergit/selectors.py    |       65 |        0 |     100% |           |
| layergit/worktree.py     |      119 |        0 |     100% |           |
| **TOTAL**                | **1381** |    **0** | **100%** |           |

## Excluded Defensive Lines

The coverage total excludes only the following `# pragma: no cover` defensive
fallbacks. These lines are not reachable through the public CLI/parser paths
today, but they are intentionally kept as guardrails if future code changes
break an invariant or call helpers directly.

| File | Line | Why excluded | Why keep it |
| ---- | ---: | ------------ | ----------- |
| `layergit/cli.py` | 344 | `main()` has already dispatched every command accepted by `argparse`, so this fallback is unreachable unless a future command is added to the parser without dispatch wiring. | Returning `0` keeps the function total and prevents accidental `None` returns if dispatch is refactored. |
| `layergit/cli.py` | 360 | `argparse` raises `SystemExit` when command-specific `--help` is parsed, so normal `layer help <command>` never reaches this return. | It documents the expected success path if a future parser implementation stops raising for help output. |
| `layergit/cli.py` | 418 | `infer_layer_name()` already returns a unique name, so the second duplicate-name branch is not reachable through normal `layer add <repo>` flow. | It preserves a safety net if `cmd_add()` is called directly or name inference changes later. |
| `layergit/cli.py` | 504 | `argparse` restricts `layer move` positions to `top`, `bottom`, `up`, `down`, `before`, or `after`, so no other value reaches this branch through the CLI. | It protects direct/internal callers and future parser changes from silently accepting an unknown movement action. |
| `layergit/selectors.py` | 79 | `insertion_index()` already returns for default/top, `before`, `after`, and invalid `before+after` cases. | The final return keeps the helper exhaustive if the condition structure is changed later. |
