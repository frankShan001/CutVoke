# Automated tests

Run `python -m pytest -q` from the repository root after installing `.[dev]`.
FFmpeg/ffprobe must be on PATH for rendering tests. Generated fixtures use
temporary directories; personal projects and downloaded media are not required.

| Directory | Coverage |
| --- | --- |
| `domain/` | Project models, time and invariants |
| `editing/` | Commands, transactions, undo/redo and shared editing |
| `rendering/` | Frames, effects, composition, preview and export |
| `storage/` | Persistence, resources, paths and project archives |
| `interfaces/` | CLI, HTTP, MCP and security boundaries |
| `integration/` | Packaging, contributions and repository checks |
| `support/` | Isolated browser-test service |

Browser tests live in `apps/editor/e2e/`; desktop tests live in `apps/desktop/`.
See [contributing](../CONTRIBUTING.md) for their commands. Development and build
commands belong in `tools/`; test reports and generated media do not belong in Git.
