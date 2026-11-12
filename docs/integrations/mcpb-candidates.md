# Offline native MCPB candidates

This is preparation for [#47](https://github.com/frankShan001/CutVoke/issues/47),
not a published MCP Registry package. It does not close that issue.

The standard-library-only builder consumes an **extracted portable staging tree**
produced by `tools/build/assemble_portable.py`. Start with a qualified native build
at a recorded commit. Do not point it at a source checkout, Electron launcher,
incomplete PyInstaller directory, or arbitrary untrusted archive. Extraction and
qualification are separate steps; this command does not extract downloads or run
any input executable. Use a private, immutable staging directory during the build.

```sh
python tools/build/build_mcpb.py /path/to/CutVoke-0.3.5-linux-x64 /outside/staging/CutVoke-0.3.5-linux-x64.mcpb
python tests/packaging/test_mcpb.py -v
```

The command prints JSON containing the output path, SHA256, platform, version,
source commit and `runtime_verified: false`. Save that output with candidate
records. A pre-existing destination is never overwritten. Output is published
atomically only after successful archive creation and checksum verification.
No network requests, dependency installation, Registry login, credentials,
release upload or `server.json` generation occur.

## What is preserved and checked

- Complete `resources/engine` (macOS: `CutVoke.app/Contents/Resources/engine`),
  including `_internal`, renderer dependencies, bundled assets, FFmpeg and ffprobe.
- Complete portable `licenses` tree, including FFmpeg corresponding source,
  Python and dependency notices, plus `LICENSE.txt` and original `build-info.json`.
- All selected files must match the portable `file-manifest.json`; missing listed
  dependencies, modified bytes and unlisted files fail. The input manifest is not
  copied unchanged because engine paths are relocated in the bundle.
- Ordinary permission bits and safe relative in-tree symlinks are retained in ZIP
  metadata. Absolute/escaping links, symlinked payload roots/ancestors, special
  files and unsafe archive paths are rejected. Unix executable mode is required
  for engine/FFmpeg/ffprobe. Privileged permission bits are removed.
- Regular-file bytes are rehashed as they are written to the archive. This is not
  a secure sandbox for concurrently hostile input: staging must stay private and
  immutable, including symlink topology. Input hashes establish consistency with
  the portable manifest, **not authenticity or native runtime qualification**.

The manifest uses MCPB 0.3 `binary`, launching the engine directly with
`mcp --data ${user_config.data_directory}` over stdio. Select persistent data
storage **outside the extension installation directory** so extension replacement
cannot remove projects. This instruction is a client configuration requirement,
not a path sandbox or an enforced boundary in this initial builder.

## Before any distribution or Registry publication

The fixture tests validate packaging logic, not working native binaries. This
change has **no MCPB host installation or native-runtime acceptance yet**.
Keep candidates unpublished until all applicable checks below are recorded:

1. Validate `manifest.json` against the official MCPB schema/CLI.
2. On Windows x64, Linux x64, macOS x64 and macOS arm64, install the matching
   candidate through the intended MCPB host. Verify executable-mode and symlink
   extraction behavior. MCPB OS compatibility does not distinguish CPU
   architecture: use clearly named per-architecture candidates and test selection.
3. Launch the manifest command without system Python, Node or FFmpeg. Exercise
   MCP initialize, tools/list, project edit, actual preview/export and clean
   shutdown with Chinese/space-containing data paths. Inspect exported media.
4. Replace the extension and verify external project data survives. Confirm
   client prompts and permissions, and test OS signing/quarantine behavior.
5. Only after immutable release URLs and final bytes exist, generate accurate
   Registry metadata using the actual SHA256 and URL. Publishing requires the
   separate maintainer release/Registry workflow; this tool does not do it.

Specification: <https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md>.
