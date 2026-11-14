# Same-snapshot dense-export recovery

This first phase of #32 only reuses successfully encoded, verified lossless video
windows in the existing dense-timeline export path. It is not incremental editing,
a general render cache, a persistent export queue retry command, or a completed
implementation of #32. Ordinary short/sparse exports are unchanged.

Retries into the same output directory can recover completed windows, even after
a failure/cancellation/process restart. Output names do not affect reuse. The
immutable original project, source file content, requested
range, render options, FFmpeg version/build and all CutVoke Python source identities identify
work. Source stat identity is also checked before publication and reuse to reject
files changing during the attempt. Changing the snapshot invalidates all its windows in this phase. Hashing
source files costs one sequential read per attempt; media is never uploaded.

Only ordinary file-backed clips are eligible. Captions, effects (including
transitions), text/other specialized roles, compound sequences, linked properties,
resource references and GPU compilation explicitly bypass reuse. Supporting those
requires a separate dependency-identity contract, not hashing preparation paths. Frozen/native packages without readable renderer
source files also safely bypass recovery; this phase does not guarantee packaged
application reuse.

Audio is always mixed once over the entire requested range. Each window is still
compiled to preserve diagnostics; a cache hit only skips its FFV1 encoding. Video
seams, final encoding, final media verification and continuous-audio verification
are not skipped. `reusedWindowCount` reports actual reuse, not completed exports.
A draft PR or a cache hit is not proof that the original issue is resolved.

## Storage and failure contract

The fixed `.cutvoke-render-cache/windows.sqlite3` location is derived from the
already-authorized output directory, never a cache path in project JSON. Only
this disposable database is pruned. Removing it while no export is running is
safe; subsequent exports simply render again. Cache read/write errors are misses.

SQLite transactions atomically publish blobs and serialize LRU eviction with
other processes. There are no shared media paths or persistent partial files:
readers take private copies into their own temporary export directory, then check
SHA-256 and media metadata. Cancellation deletes that request's temporary files;
previously committed windows remain reusable. Changed sources disable publication.

Limits per output directory: 64 windows, 64 MiB per window, 256 MiB total payload.
Larger windows render normally without caching. The database additionally caps
allocated pages at 320 MiB and uses full auto-vacuum and rollback journaling,
not an unbounded WAL. A transaction's rollback journal can temporarily take up to
another database-sized allocation. Memory includes up to 64 MiB per concurrent
cache operation plus SQLite/encoder buffers. Contention times out to ordinary
rendering, rather than blocking an export indefinitely. Limits do not aggregate
across different output directories. Do not describe them as a global disk quota.

Python source identities include every `.py` file under the CutVoke package,
including domain timing/keyframes and preprocessing. Bump the cache ABI when
changing rendering dependencies/semantics outside that package. Phase two should introduce explicit cache
settings, broader dependency identities and local-edit invalidation only with
matching real-media correctness tests.

## Verification

`tests/rendering/test_window_cache.py` exercises failure/cancel recovery, exact
cold-vs-resumed decoded RGB/PCM equality, fractional-rate range exports, source
and tool/ABI invalidation, corrupt/unavailable databases, capacity, concurrent
publication and concurrent real exports. Existing continuous audio, seam/color
and dense-window tests remain required. There is no new UI in this phase.
