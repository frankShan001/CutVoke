# Issue 55: incompatible format migration work in progress

The owner chose an incompatible new project format. New versions must identify
old projects and offer a visible **Convert copy and open** or **Close** choice.
Reading or closing must never silently convert the original.

## Current implementation boundary

Draft implementation now includes read-only discovery, explicit copy conversion,
v2 manifest/database with isolated authoritative tables, catalogue copy before
writable initialization, recent/directory UI choice, and signed-local complete
manual curves for reversible trims. The old source remains separate. Generic
legacy clients can create empty legacy tables if explicitly pointed at the raw
new SQLite file, but cannot read/overwrite the authoritative cv2 tables. Normal
legacy directory/package readers reject the changed container versions.

Conversion gives the copy a new identity and keeps history. It refuses active
leases/jobs; completed old job receipts and idempotency records are not replayed
in the new identity. Source token changes abort publication. Failed destinations
remain `preparing` and cannot be opened. Original project media is not deleted.

This is work in progress, not merge-ready. Legacy package opening now has the
same explicit Convert copy / Close choice: a token-bound immutable archive copy
is preflighted and must pass complete-media validation before registration.
The original directory copy-switch and rollback functions refuse in-place
operation. Global library relocation understands v2 child databases/WAL and
active ASR/export/lease guards. Declared file resources, asset-ID-only references,
history and generated source ZIP companions are copied without executing source.
Global fonts/plugins referenced by installation names remain runtime dependencies,
as before; this does not claim a fully standalone bundle of all installed tools.

Actual old source acceptance was run against both 879841a and released v0.3.5:
directory/package readers refuse v2, and raw old-store writes cannot alter the
v2 authoritative snapshot. Focused integrated migration/package/source/history
checks passed 97 tests and 5 subtests after the follow-up fixes. Full CI/platform
and actual new conversion browser end-to-end acceptance are not yet complete.

The project-format envelope codec is currently used for package boundaries;
the SQLite boundary uses namespaced tables rather than the envelope.

## Required integration sequence

1. Read-only JSON/SQLite detection BEFORE constructing ProjectStore or
   DirectoryProjectStore. Existing constructors write schema/WAL and can rebase
   paths; using them to detect an old file violates Close's no-change contract.
2. A dedicated PROJECT_CONVERSION_REQUIRED outcome, not ValueError/TypeError:
   existing Store.load catches those and falls back to sequence_json.
3. Guard every opening path: recent/home selection, directory opening, HTTP,
   MCP, package imports, CLI, clone/import, plus background job activation.
   Non-UI access must return conversion-required, never imply user consent.
4. UI choice shows source version, destination and retained original. Close
   does not register/open/start preview or background tasks.
5. Use a fresh destination and SQLite backup API (including committed WAL),
   validated source-state token, preserved media/history and no source changes.
   Recheck source token after copying. Failed output stays non-ready.
6. New manifest version 2, different database filename and isolated storage
   layout. Do not retain a readable legacy projects/sequence_json mirror.
   Version bump alone is insufficient: shipped readers ignore schemaVersion.
7. Migrate current state AND undo/redo snapshots; explicitly handle queued
   export/ASR snapshots without allowing schema-1 state to re-enter editing.
8. Validate all copied state before atomic ready/registration. Original project
   remains usable by the old version; the converted copy belongs to the new one.
9. Introduce signed local manual-lane clocks without shifting existing nodes on old projects.
   Preserve current visible old animation, not an inferred historical intent.
   New trim/extend commands update clock exactly; move/slip do not. Derived
   controller lanes must not shift twice. All render/UI/cache consumers must agree.

## Acceptance still required

- Real released reader fixtures: directory, raw database and package must refuse
  new-format data; verify hashes unchanged after failed open/write attempts.
- Legacy/future/corrupt detection, Close, repeated conversion clicks, stale token,
  concurrent writer, missing media, disk/copy failure, interrupted process,
  restart, existing destination, and successful original/copy independence.
- SQLite save/reopen and persisted undo/redo across trim-in/extend-back, split,
  edits/deletion, reframe, sequence duplication and import/export round trips.
- Five interpolation modes incl hold jumps at 24 and NTSC; effects, transform,
  opacity/volume; constant/reverse/curved speed and freeze behavior.
- Browser preview and real product export frames/audio; exact-head full CI and
  platform packages. Isolated codec tests or passing #56 prototype are not these.

## Historical schema spellings

Both `1` and `1.0` are recognized as legacy project schemas, never current.
The pre-v2 model accepted `1.0`, and repository VFR/transition fixtures exercise
that representation. Detection reports the exact `sourceSchemaVersion` alongside
container generation `sourceVersion: 1`. Explicit copy conversion upgrades current
and history snapshots to `2`; the original schema string and source bytes remain
unchanged. The service still refuses to edit either legacy spelling. Unknown
strings such as `1.1`, `1.0.0` and `2.0` are not guessed compatible.

### Package conversion publication gate

Legacy package conversion validates a private extracted snapshot before creating
its requested destination. It rejects symlink targets/ancestors, colliding or
non-portable member names, conflicting original-path mappings, missing/checksum
failures and stale original-package tokens. Exclusive publication never replaces
an existing destination; IO failure removes only files/directories created by
that attempt and retains concurrent writers' objects. Registration happens only
after publication returns. Installed fonts/plugins remain runtime dependencies.

The real browser conversion test covers Close (no catalogue/source change) and
Convert (new schema-2 identity, original unchanged) for directories and packages.
Local browser execution may be unavailable in restricted cloud environments;
test discovery or fixture validation is not a substitute for CI browser passage.

### Sidecar-free source inspection

SQLite `mode=ro` is not a no-write guarantee for WAL bookkeeping: it can create
`-wal`/`-shm` beside an otherwise unchanged source. Inspection and conversion now
copy a stable observation of DB plus existing WAL into private temporary storage,
verify source identity/bytes before use, and let SQLite rebuild bookkeeping only
there. Committed WAL transactions remain visible; uncommitted frames do not.
Continuously changing sources and nonempty rollback journals are refused rather
than guessed. Never substitute `immutable=1`, which would ignore existing WAL.
