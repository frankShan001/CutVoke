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

This is work in progress, not merge-ready. The original directory migration and
rollback endpoints are disabled in favor of the explicit format conversion.
Old package import is fail-closed pending its own explicit conversion UI; users
must convert the original project then export a v2 package. Global library
relocation and old migration test contracts still need updating for the new
storage layout. Full CI/platform and actual browser acceptance are not complete.

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
