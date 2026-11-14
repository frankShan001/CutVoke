# Parameterized external renders and verified reuse

`generated_render` plans media **before an output exists**. It validates a bounded
parameter schema, declared source/asset/font hashes and an absolute frame window;
it returns a content key and either verified managed media or an explicit cache
miss. It never executes source, installs dependencies or edits the timeline.
MCP and `POST /api/v1/agent/generated_render` use the same schema and handler.
Read `editor_help(topic="generated-render")` for the connected-tool workflow.

## Request and operations

The [runnable original-media example](../../examples/generated-media/README.md)
creates complete requests. Required request fields are:

| Field | Meaning |
| --- | --- |
| `kind`, `schemaVersion` | `cutvoke.generated-render-request`, integer `1` |
| `renderer` | Declared `name`, exact `version`, runtime/dependency `build` |
| `entrypoint`, `files` | Declared source entrypoint; relative POSIX input paths with SHA256 and source/asset/font/lockfile/license roles |
| `bindings` | Named references to declared asset/font paths; no expressions |
| `paramsSchema`, `params` | Closed flat object; all 1–32 fields required; bounded scalars or bounded scalar arrays |
| `seed`, `profile` | uint32 seed; declared `review` or `final` quality |
| `output` | Width/height, rational fps, **logical full** `durationFrames`, `format`, alpha/color/audio contract |
| `frameRange` | Absolute logical `startFrame` and `frameCount`; within full duration |

Numbers require finite minimum/maximum within ±1e12; strings require
minLength/maxLength (maximum 2048); arrays require minItems/maxItems (maximum 64)
and scalar item definitions. Scalar enums are supported. No nested objects,
references, patterns, defaults, expressions or commands. Names use ASCII letters
followed by letters/digits/underscore (maximum 32). Overrides merge only declared
parameter names and are revalidated. `frameRange` overrides replace the window.
The supplied frame-window clip starts physically at timestamp zero; animation
must use the declared absolute logical frame clock in the external renderer.

1. Call `generated_render` with `projectId`, `operation="plan"`, `requestPath`,
   optionally `params`, `frameRange`, `previousRequestKey`. Save the returned
   `requestKey` and **effective request**. `changedFields` compares a previously
   retained registered request. Planning freezes and hashes inputs, then discards
   its temporary snapshot; it does not reserve a job or store the plan.
2. On `requiresExternalRender=true`, use an independently trusted renderer to
   produce that effective request. CutVoke supplies no shell command or runner.
   A different machine must receive the declared inputs too. Neither hashes nor
   declared renderer versions prove dependency completeness or safety.
3. Call `operation="register"` with the same request/overrides,
   `expectedRequestKey`, `renderPath`, `renderSha256`. Changed source/parameters
   reject the stale key. The bridge freezes output bytes, verifies hashes,
   decoded CFR frame count/timestamps, dimensions, BT.709 and codec, imports an
   ordinary asset, and archives the effective request alongside declared inputs.
4. On a hit, use returned `assetId` and exact rational `mediaSourceStart`.
   A covering hit reuses a larger existing window: source time is
   `(requestedStartFrame - renderedStartFrame) / fps`. Use this offset in native
   `clip.insert`/`clip.trim`. `previewCall` previews source-local times through
   existing `preview_frames`; inspect the images and final composite separately.
5. Perform native changes through leases, command preview, revision and command
   ID. To replace a smaller internal window, split the existing clip at its
   boundaries, trim the middle segment's source start to the new asset's offset,
   and swap only that segment. `edit.batch` keeps this one undo point. Keep
   titles, captions, effects and sound in native editable tracks where practical.

## Cache boundaries and delivery

Exact request identity includes parameters/schema, source and binding hashes,
renderer/build, seed, profile, output contract and frame window. Covering reuse
differs only in frame window; full logical duration stays part of the identity.
These are declared dependencies; CutVoke cannot discover hidden files, network,
GPU state, temporal dependencies or runtime randomness. The sample proves its
own frame clock, not the behavior of every external renderer.

Every hit rehashes the managed media and source archive. Missing/changed bytes
produce a miss; they are never silently replaced. Two different media hashes
registered for one request block reuse for that entire retained composition,
including covering windows. This detects observed inconsistency, not all
nondeterminism. Concurrent registration uses SQLite transactions.

The project-local `media/.generated/render-index.sqlite` retains at most 256
request entries and two output identities per request. LRU eviction removes
**metadata only**, preserving media and archives. `operation="status"` reports
entries/conflicting compositions. Cache lookup is synchronous, bounded by the
per-file limits; hashing a large hit has real I/O cost. Registration can import
an asset even when its subsequent index write fails: retrying the same bytes is
safe. There is no render job, automatic scheduling or cancellation API.

Requests are at most 256 KiB; 127 declared inputs plus the generated request
must fit the bridge's 32 MiB input budget; rendered output is at most 512 MiB.
Output is silent H.264 MP4 or ProRes 4444 MOV, BT.709, square pixels, 1–120 fps,
at most 4096 per dimension, 18000 frames and 600 seconds. Straight alpha needs
ProRes; actual alpha pixels/compositing require separate inspection. Opaque MP4
dimensions must be compatible with its encoder (the reference uses yuv420p).

Keep the whole project folder to retain the index and `*.cvsource.zip` archives.
The `.cvkpkg` package carries editable media and verified source archives for
referenced outputs, including effective `inputs/cutvoke-request.json` requests.
The cache index is excluded. `generated_source` lists/inspects these archives
and explicitly restores one into a new directory without execution. Inside each
archive the effective request resolves declared paths relative to `inputs/`.
Restoring inputs does not register cache entries or rerender automatically.

There is no parameter GUI, arbitrary-code runtime or isolation guarantee in this
feature. A Docker capability probe alone is insufficient to establish safe
hostile execution: output quotas, termination/recovery and hostile-output
handling require a separate validated runner. No untrusted source is executed.
