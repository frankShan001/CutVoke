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

The generator requires fontTools 4.66.1, matching the current desktop build lock.
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
  the existing CUTVOKE_FONT_CACHE path.
- Missing, malformed or corrupt optional assets fall back to original conversion.
  No cache-key/alias contract change or arbitrary manifest path is allowed.

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
