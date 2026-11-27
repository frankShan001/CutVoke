# Issue 55: isolated animation clock spike

This is a design experiment, not a production fix or an adopted project schema.

Run against the repository:

```sh
PYTHONPATH=src python -m pytest tools/verify/prototype_animation_clock.py -q
```

Observed: 11 passed on 2026-11-13, using CutVoke base bbccca3d.

Proves only the manual-lane contract: rational offset over canonical curves
retains easing without destructive crops, permits reversible trim/extend,
local editing/retiming, independent split children, and isolated JSON roundtrip.
It does NOT prove ProjectStore migration, commands/history, browser preview,
export pixels/audio, controller expressions, source/speed/reverse mappings,
transition handles or old-version reader compatibility.

Important newly tested boundary: extend before the original origin, then edit
in that new portion, produces negative canonical key times. Existing product
invariants forbid these. Options include atomically translating every canonical
key and offset to a nonnegative origin, or explicitly versioning a signed
canonical-time model. Neither has been adopted here.

Current prototype retains full original curves independently for both split
children. This choice intentionally permits later extension. UI policy for
whether those hidden keys remain editable is not decided by this prototype.

No production code edited; no merge or release. The test is outside automatic
production tests collection. Keep the known failing trim diagnostic in PR 53
separate from this passing model experiment.

## Follow-up: coordinate normalization

The spike now includes `normalize_origin`: shift all manual key times and the
shared offset by the same exact rational value. Ten more parametrized tests
prove nonnegative keys, idempotence, preservation of an untouched lane and IDs,
25 trim/extend cycles without drift, JSON reopen, split and subsequent edits.
Observed total: **21 passed**, 2026-11-14.

This resolves the negative-key case in the isolated coordinate model, not the
production bug. Offset itself can legitimately remain negative before the first
key; all readers must use the offset. An old reader ignoring it would still
misrender. A safe integration still needs schema/version policy, atomic command
history persistence, renderer/browser parity and derived-lane exclusion. No
production model change is included or implied by these passing tests.

## Legacy-reader materialized fallback probe

Five additional tests use the real existing Clip reader and ProjectStore SQLite
save/close/reopen. A visible local crop alongside hypothetical recovery metadata
preserves visible interpolation in all five modes, but the old reader discards
recovery metadata. Total: **26 passed**. This is a compatibility experiment for
an alternative dual representation, not adoption of it. Reversible extension
cannot survive an old-reader save unless another explicit protocol preserves it;
never silently restore a stale sidecar after old-client edits. ProjectStore
accepts the future schema marker `999`, confirming a bump is not a reader gate.

## Guarded recovery codec experiment

`encode_compat` materializes local visible curves; `decode_compat` accepts the
canonical recovery only when rematerializing it exactly reproduces ALL visible
lanes. Five mode tests reject stale value/deletion/retime/duration edits and
unknown recovery versions, falling back to visible data instead of resurrecting
stale animation. JSON roundtrip retains reversible extension when metadata is
intact. Total **31 passed**. This is conservative consistency checking, not a
security/authentication mechanism; innocuous serialization differences may lose
recovery. Old-client writes that discard metadata still lose reversibility.
This protocol remains isolated and is not adopted by production commands.

## Malformed optional recovery regression

A malformed `prototypeRecovery.lanes` container (including a list or null), or
non-object key entries, previously raised `AttributeError` instead of retaining
valid visible motion. Structural checks now reject these optional recovery
payloads before parsing. Six regression cases preserve all sampled visible
values and leave input data unchanged. Cloud validation: **37 passed** with
`PYTHONPATH=src python -m pytest tools/verify/prototype_animation_clock.py -q`.
Only the isolated prototype changed. This is not a production trim fix, nor
approval of the dual-representation format; old-client saves still lose recovery.
