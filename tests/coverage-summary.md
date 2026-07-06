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
| layergit/cli.py          |      570 |        0 |     100% |           |
| layergit/composer.py     |      230 |        0 |     100% |           |
| layergit/errors.py       |        1 |        0 |     100% |           |
| layergit/exporter.py     |       23 |        0 |     100% |           |
| layergit/gitops.py       |       87 |        0 |     100% |           |
| layergit/manifest.py     |       50 |        0 |     100% |           |
| layergit/merger.py       |       34 |        0 |     100% |           |
| layergit/reports.py      |      329 |        0 |     100% |           |
| layergit/selectors.py    |       65 |        0 |     100% |           |
| layergit/worktree.py     |      119 |        0 |     100% |           |
| **TOTAL**                | **1509** |    **0** | **100%** |           |

## Coverage pragmas

These `# pragma: no cover` lines are intentionally excluded because they are
defensive fallbacks for states the parser or helper functions prevent during
normal execution. They are still useful guardrails for future refactors.

| File | Why unreachable today | Why keep it |
| ---- | --------------------- | ----------- |
| `layergit/cli.py` final `main()` return | `argparse` only accepts known commands, and `main()` dispatches each known command before that line. | It prevents an accidental `None` return if a future command is added to the parser without dispatch wiring. |
| `layergit/cli.py` final `cmd_help()` return | `argparse` raises `SystemExit` for command-specific `--help`, so normal help flow exits before that line. | It keeps `cmd_help()` total if argparse behavior or command-help plumbing changes later. |
| `layergit/cli.py` duplicate inferred layer-name fallback | `infer_layer_name()` already returns a unique name when no explicit name is provided. | It protects direct/internal callers or future inference changes from silently accepting a duplicate layer name. |
| `layergit/cli.py` unknown `move` position fallback | `argparse` choices restrict move positions to `top`, `bottom`, `up`, `down`, `before`, or `after`. | It protects direct/internal callers and future parser changes from accepting an unknown movement action. |
| `layergit/selectors.py` insertion-index fallback | Current insertion paths return for `before`, `after`, explicit `top`, and default top insertion. | It keeps the helper exhaustive if the condition structure is changed later. |
