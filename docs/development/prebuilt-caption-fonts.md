# Draft: complete prebuilt caption fonts (#51)

This is a production-path draft, not release approval or a claim that #51 is closed.
It does not subset user text. It moves complete bundled Noto Sans SC 400/700
conversion into packaging and preserves all original glyph and layout tables.

## Explicit tradeoff and provenance

The two generated files are 21,182,328 bytes uncompressed. Measured zlib-9 sizes
are 6,304,103 + 6,382,625 bytes (about 12.10 MiB); actual installer growth is not
measured. Other font families and custom fonts retain original runtime conversion.
Original source assets remain unchanged and shipped. Generated resources retain
font copyright/name records other than the existing caption-family rename, and
copy the original OFL-Sans.txt alongside a digest manifest. No binary is committed.

The generator pins fontTools 4.59.0 on Python 3.10 and 4.66.1 on Python >=3.11
(the latter matches the current desktop lock). The build-system uses matching
PEP508 markers; Python 3.10 remains supported. All axes are explicitly pinned
and fvar absence is checked, without the newer static keyword unavailable in
fontTools 4.59.0. Reproducibility is within a pinned toolchain, not across versions.
It pins the source SHA256 and disables timestamp recalculation. Source updates
require review of this recipe, not silent generation with another font.

## Packaging and runtime

- Non-editable wheel build_py invokes the generator before package data discovery.
  The build-system requires the pinned fontTools version.
- The desktop engine.spec invokes the same generator before collecting assets,
  covering the common Windows/macOS/Linux PyInstaller entry point.
- Existing package-data globs include assets/*/*.ttf, *.json and *.txt.
- Editable/source runs without generated assets fall back to the old converter.
  No startup font download or new user operation is needed.
- Runtime uses packaged data only for the exact bundled source path and digest,
  never when CUTVOKE_FONT is set. It checks resource hash, filename, license hash,
  static status, family alias, weight and metrics, then atomically publishes into
  the existing CUTVOKE_FONT_CACHE directory. Packaged filenames use v2 plus the
  verified resource SHA256; fallback retains v1. Different fontTools outputs
  must not share an entry, even if source/weight match.
- Missing, malformed or corrupt optional assets fall back to original conversion.
  Family aliases remain unchanged; no arbitrary manifest path is allowed.

## Actual Linux verification

Python 3.12.14/fontTools 4.66.1:
- Production _caption_font_instance with a new cache: 400 = 0.159s,
  700 = 0.038s, correct existing cache paths/static metadata.
- Explicit CUTVOKE_FONT fallback: 400 = 12.344s, 700 = 11.681s.
  Compared generated outputs to fallback outputs: every table except head was
  byte-identical. head contains timestamp/checksum differences; unitsPerEm matched.
- Runtime guard tests cover custom/system sources, missing/invalid manifests,
  wrong source/weight, corrupt font/license, traversal, atomic publication,
  failure cleanup and semantic-validation fallback.

## Remaining gates

The common packaging entry points were inspected, not executed end-to-end on
all platforms. Need real wheel and sdist-to-wheel installs, fresh-cache browser
title-edit/save/undo/IME at unchanged 45s, Windows/macOS/Linux packaged app tests,
installer size measurement, license review and reproducibility on separate hosts.
The earlier #61 subset/libass experiment is not acceptance of this production
draft. Do not merge until review and the relevant delivery checks pass.

## Review follow-up: verify the actual resource path

verify_distribution now requires both font files, manifest and OFL in the wheel,
and checks source/output/license hashes and original-license equality. The source
archive must contain generator and runtime verifier.

verify_packaged_caption_fonts.py uses actual packaged files with a fresh isolated
cache for both weights, forbids runtime instantiateVariableFont, and compares the
result byte-for-byte to the verified resource. --require-installed rejects the
checkout source package. CI calls it after wheel installation. Browser CI explicitly
generates and verifies assets before scenarios; it is still an editable-source
browser run, not proof of a native packaged-app experience. The original fallback
remains covered separately. Missing resources must fail this verifier, not pass
by falling back. A green unrelated CI step is not proof of this path.

## Cross-version review and cache isolation

4.59.0 and 4.66.1 outputs differ in BASE/GPOS/GSUB, not just head timestamps.
For example BASE is 266 versus 210 bytes. Therefore packaged cache keys now
include the verified output SHA256. This avoids reusing a prior toolchain's cached
output; unchanged legacy fallback continues using v1. A regression verifies two
different packaged byte sequences cannot address the same cache file. The cache
root and public return interface do not change.

Actual Linux follow-up:
- 4.59.0 built resources versus its own CUTVOKE_FONT fallback: all non-head
  tables byte-identical for both weights (fallback took 19.126/16.129s in this run).
- 4.66.1 same-version table comparison was already recorded above.
- Eight real libass render cases: both weights × 4.59/4.66 packaged/fallback.
  Seven fixed synthetic strings at 1280x720,32px,2pxoutline produced identical
  frames and HarfBuzz glyph-ID/cluster/position tuples in all eight cases.
  Fontselect was checked and frames were nonblack. These limited fixtures do
  NOT prove all-Unicode layout equivalence, hence cache isolation remains needed.
- Extracted the actual Python3.10 sdist from the prior candidate and built a wheel
  in an isolated environment, installed it, and passed --require-installed with
  runtime conversion forbidden for both weights (0.097/0.040s). This roundtrip
  tests the pre-cache-isolation candidate, not a claim that later edits were
  present in that archive.
- Latest source cache-isolation tests:22 passed,8subtests; real-resource verifier
  passes with runtime conversion forbidden. Cross-platform/native-app and latest
  head packaged browser acceptance remain pending; no merge approval.

## Warm-cache integrity review

v2 cache hits now hash cached bytes against the already-verified packaged bytes,
in addition to font/alias/metric checks. Invalid or truncated cached data is
replaced atomically from those bytes; the old entry remains until replacement
succeeds. Legacy v1 fallback behavior is unchanged. This is corruption detection,
not a signature or protection against a modified application/resource installation.

Regression fixtures use real parseable static fonts and cover unchanged warm hits,
truncation and altered horizontal metrics with unchanged alias. Damaged entries
are restored exactly without invoking the instancer, unchanged hits do not replace.
Latest focused result:25 tests and8subtests. Actual complete400/700 assets also
passed both truncation and parseable-metric corruption repair with conversion
forbidden. This does not replace native distribution/browser acceptance.
