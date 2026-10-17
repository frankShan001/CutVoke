# Independent moving mattes

`cutvoke.fx.mask` supports `rect`, `circle`, `freehand`, `text`, and the new directional `wipe` half-plane. The picture stays stationary while the alpha matte moves. Existing static projects retain their previous schema values and pixels.

Set `motion` to `minimum-jerk`, `inertia`, or `linear` (`none` preserves static behaviour). Times are clip-local presentation seconds after speed retiming: `motionStart`, `motionDuration`. Positions `motionFromX/Y` and `motionToX/Y` are offsets in canvas fractions. Scale and rotation use the canvas centre as pivot: `motionFromScale`, `motionToScale`, `motionFromRotation`, `motionToRotation`. All endpoints hold outside the motion interval. `angle` sets a wipe edge normal in degrees; feather and invert work with motion.

* Minimum jerk: q(u) = 10u^3 - 15u^4 + 6u^5. Velocity and acceleration are zero at both endpoints.
* Inertia: constant acceleration for fraction r, uniform cruise, then equal constant braking, with continuous position and velocity. `motionRamp` sets r between .05 and .45. This is a prescribed kinematic trajectory, not a rigid-body simulation or automatic subject tracker.
* Linear: constant velocity, for deliberate mechanical sweeps.

Example: reveal the next shot through a feathered diagonal edge in 0.75 seconds:

```json
{"shape":"wipe","x":0.5,"y":0.5,"angle":-17,"feather":0.065,
 "motion":"minimum-jerk","motionDuration":0.75,
 "motionFromX":-1,"motionToX":1}
```

The registry drives the same controls in the Web inspector, CLI, HTTP API and fresh MCP sessions. Toggle motion off to edit static path handles on the canvas, then set motion offsets in the inspector. Animated mattes and half-plane wipes do not display misleading static canvas handles. Save, undo, reload, preview seeking, and export use the same native effect parameters. Static PNG/freehand caching explicitly excludes animated mattes. No mask frames are baked into project media.

Tests render actual moving alpha for rectangles, freehand paths, text and wipes, check the unmoving source gradient, persistence/undo, rest-to-rest velocities, and preview-window/export parity.
