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
