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
