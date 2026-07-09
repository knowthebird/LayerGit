# Demos

## Overlap Demo

The current checked-in demo is local and network-free after dependencies are
installed:

```bash
examples/overlap-demo.sh
```

It creates temporary Git repositories, initializes a LayerGit workspace, and
demonstrates:

- overlapping paths
- top-layer-wins precedence
- masked lower-layer files
- `layer explain`
- `layer use`
- applying a buildtree edit back to a layer
- export

## Planned Vendor / Board / App Demo

TODO: add `examples/vendor-board-app/` when the richer demo stabilizes.
When it exists, add a matching verification script that can be run from the repo
root and checks the documented masking, mount-path, apply, delete, and doctor
behavior.

The intended scenario:

- `vendor-sdk` mounted at `/`
- `board-support` mounted at `/`
- `app` mounted at `/app`
- optional `local-edits` mounted at `/`

It should show top-layer-wins masking, mount paths, hidden inherited files,
provenance, `doctor`, and safe apply/apply-to/delete behavior.

## Visual Media

The text demo should remain the source of truth. Optional media can include:

- terminal GIF
- short VS Code GIF
- asciinema cast
- static screenshot
