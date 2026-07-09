# Examples

These examples are safe to run locally and do not require network access.

## Overlap Demo

`overlap-demo.sh` creates a temporary LayerGit workspace and two local Git repos.
Both repos provide `common/util.c`. The demo shows that the top layer wins by
default, lower copies remain recorded as masked provenance, `layer use` can
select a lower layer for one path, and the composed result can be exported.

Run it from the repo root:

```bash
examples/overlap-demo.sh
```
