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
- Ordinary permission bits are retained. Verified relative in-tree file symlinks
  are materialized as regular files with the resolved target bytes and mode,
  because official CLI unpack does not recreate symlinks. Directory aliases
  are recursively materialized using canonical target files verified against the
  portable manifest; cycles and broken links are rejected.
  Absolute/escaping links, symlinked payload roots/ancestors, special files and
  unsafe archive paths are rejected. Unix executable mode is required
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

## Official CLI probe (2026-11-12)

Using `@anthropic-ai/mcpb@2.1.2` (the npm package named by the official
`modelcontextprotocol/mcpb` repository), all four synthetic-layout manifests
passed `mcpb validate`. This proves schema compatibility, not runtime readiness.

`mcpb unpack` exposed an actual compatibility defect: explicit directory ZIP
entries caused `EISDIR`. The builder now omits directory entries; parent folders
are reconstructed from file paths (empty directories are not retained). The
corrected Linux synthetic bundle unpacks successfully and restores the engine's
`0755` mode. Packaging contract tests assert that no directory entries recur.

The same CLI also turns ZIP symlink entries into regular files containing their
target text. The builder now materializes verified in-tree file links as regular
files, using the resolved target's ordinary permission bits and rehashing the
actual bytes while writing against the link's portable manifest checksum. The
validated resolved path is retained instead of following a subsequently retargeted
link. Directory aliases (including macOS Python.framework paths) now expand
recursively into ordinary files. The portable manifest does not enumerate files
under directory aliases, so each expanded file uses its canonical target checksum;
original file aliases must also match their own manifest entry. Cycles, escaping
or broken links, unlisted files and special nodes fail. Immutable private staging
remains required.

A synthetic Linux bundle with a library link now passes official CLI unpack and
manifest validation; the extracted alias is a regular file byte-identical to its
target with mode 0755. Contract tests cover materialization, corruption rejection,
link retargeting, framework directory-alias expansion, invalid directory targets
and deterministic bytes.

These probes use synthetic files, not native executables. They do not establish
MCPB host installation, actual runtime operation, or cross-platform acceptance.

## Before any distribution or Registry publication

The fixture tests validate packaging logic, not working native binaries. The
separate automated native smoke gate below can establish native stdio and
same-version reinstall acceptance for a specific commit; consult its exact-head
results rather than treating fixture tests as runtime evidence. Desktop MCPB host
installation, signing/quarantine and cross-version migration remain separate
acceptance requirements. Keep candidates unpublished until all applicable checks
below are recorded:

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

## Automated native MCPB smoke gate

The native package workflows run `tools/verify/verify_native_mcpb.py` after
portable acceptance on Windows x64, Linux x64, macOS x64 and macOS arm64.
The verifier requires staging provenance to match the checkout and runner
architecture. It builds a local candidate, unpacks and validates it with the
official pinned MCPB CLI 2.1.2, then launches the manifest command with an empty
PATH and an external Chinese/space-containing data directory.

The test covers stdio discovery, import, a leased timeline edit, actual PNG
preview and H.264 export, dimensions/duration and decoded red-frame pixels.
A second fresh extraction must reopen the exact persisted project and render
again. Only logs, probes and synthetic test pictures are uploaded as evidence;
the candidate and unpacked native payload are not uploaded by this gate.

Adding this gate is not evidence that it passed: inspect each exact-head run's
`*-mcpb-evidence/result.json` and all native job conclusions. This is a native
stdio / same-version reinstall test, not desktop MCPB host installation,
architecture-selection UX, signing/quarantine acceptance or migration across
versions. Those checks and the issue remain open.
