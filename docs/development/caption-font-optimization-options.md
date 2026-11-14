# Draft: cold caption font options for #51

Decision record under review, NOT an adopted resource format or production fix.
No runtime, dependency declaration, font asset, timeout or visual policy changes.

## Measurements

Linux cloud, Python 3.12.14, fontTools 4.68.0, uharfbuzz 0.57.0.
NotoSansSC-VF.ttf SHA256 d68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964.
Existing cold 400/700 font generation took 12.576/12.013 seconds, warm lookup
0.00051/0.00142 seconds. A separately profiled 700 instantiation took 18.602s
(instrumented): glyf/gvar 15.918s for 31,036 glyphs; OTL 2.091s.
These cloud timings do not replace the hosted #59 evidence or browser acceptance.

### A. Ship complete static instances

Two existing-algorithm outputs total 21,182,328 bytes uncompressed. Independent
zlib level-9 sizes: 400 = 6,303,652; 700 = 6,382,236 bytes; total approximately
12.10 MiB compressed. This is not a measured installer size and excludes manifests,
container headers, Serif and other font families. Highest confidence in glyph
coverage: use exactly the existing conversion, preserve original variable fonts.
Requires source/output digests and recipe version, generated-asset packaging,
license/provenance and stale/corrupt-resource fallback. Custom CUTVOKE_FONT must
never silently select packaged output for another source.

### B. Subset before instantiation

The probe requests all layout features/scripts, but the subsetter still prunes
them according to the selected glyph closure. It requests retained glyph IDs,
.notdef outlines and NFC/NFD closure. With seven synthetic text fixtures it took
0.549s subsetting plus 0.398s instantiation. Output: 320,684 bytes; zlib level-9
10,104 bytes. Two repeated same-environment builds were byte-identical with
recalcTimestamp=False: SHA256
64df65670c44d3b1cc6b517fde6c6541c5217005b002aa3642d50beba4cf6b46.
The test compares actual HarfBuzz glyph IDs/clusters/positions and exact outlines
and horizontal metrics of 34 selected glyphs against the complete static font.
This does not compare hinting instructions or libass rasterization.

Two actual failed experiments matter:
- Raw input characters plus full GSUB closure alone changed the shaping of
  `A` + combining acute. Adding NFC/NFD characters fixed this fixture.
- Default subsetting dropped .notdef's outline even though shaping IDs matched.
  `notdef_outline=True` restored the exact outline.
Format-3 post glyph names can be synthesized differently after cmap reduction;
comparison uses preserved GIDs, not synthesized Python names.

Arabic/Devanagari fixtures have 7/4 missing glyphs even in the baseline; the mixed
Japanese/Hangul fixture has 2. Equal missing-glyph results do NOT establish those
scripts are supported. A real fallback chain must be compared separately.

## Reproduction

Install fontTools 4.68.0 and uharfbuzz 0.57.0 only in an isolated experiment venv;
these are not proposed runtime dependencies. Generate the full 700 baseline with
current `_caption_font_instance('Noto Sans SC', True)` in a fresh explicit
CUTVOKE_FONT_CACHE, then pass its returned file:

```
python tools/verify/probe_caption_subset.py --source src/cutvoke/assets/fonts/NotoSansSC-VF.ttf --baseline /absolute/path/to/full-700.ttf \
  --expected-source-sha256 d68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964 \
  --expected-baseline-sha256 <digest-recorded-at-baseline-generation>
```

The probe fails with a nonzero exit even under `python -O`. It verifies supplied
input digests, a weight-only variable source covering 700, and a static 700 baseline,
then reports input hashes, axes/weight classes, Python/fontTools/uharfbuzz/HarfBuzz
versions. Record the baseline digest during trusted baseline generation; accepting
an arbitrary input's own hash does not establish its provenance. For the measured
baseline above the digest was
`2c1ee6fbd1a57bdaa6950ccec3ef5888e7f88cd2c683a6e24ae2e69a10b2c2ab`;
another build may differ due to timestamps. Metadata and hash checks alone do not
prove that a baseline was generated from this source by the required recipe.

## Gates before adopting B

- Establish closure for normalization, shaping substitutions, variation selectors,
  combining marks, bidi/script/language features and font fallback on real libass.
  Preserve .notdef, hinting, layout and metrics; no arbitrary table stripping.
- Render identical multilingual synthetic title/caption frames with full and
  subset fonts through existing FFmpeg/libass; pixel and baseline tests, both
  weights, size/stroke/rotation, mixed lines, font families and custom fonts.
- Key cache by exact source digest, face index, all axes/weight, closure set,
  shaping/subsetter versions and recipe. A text edit expanding the set must never
  reuse insufficient glyph coverage. Do not include plaintext user text in names
  or logs. Do not upload generated user-text subsets in diagnostics.
- Publish atomically; validate cached file/digest/coverage. Handle corrupt files,
  cancellation, same-key parallel builds and bounded count/bytes/eviction without
  deleting active files. Fall back to full original conversion on unsupported
  cases. No unbounded per-edit cache and no old-font alias collision in libass.
- Preserve source copyright/license/name metadata. Distribution must retain the
  existing OFL text and attribution; review reserved-name restrictions and use
  the existing renamed caption family convention for derivatives. Do not treat
  custom fonts as redistributable by default.
- Same-machine deterministic output is proved only for this pinned experiment.
  Cross-platform reproducibility requires pinned toolchain, fixed timestamps,
  deterministic glyph ordering/name records and two clean independent builds.
  Existing default TTFont saves can update modified time; do not call them
  reproducible without controlling and testing it.
- Execute the full title edit/save/undo/IME browser flow at unchanged 45s, plus
  cold loaded-runner, native targets and cancellation. Neither this probe nor
  a green unrelated CI proves #51 resolved.

Recommendation: evaluate B behind an isolated prototype first, not production.
It offers much less storage but materially more shaping/cache validation than A.
Choose A for the simplest complete-coverage behavior if the measured package
cost is accepted; do not silently choose the distribution tradeoff here.

## Follow-up: synthetic libass pixel experiment

`tools/verify/probe_caption_subset_pixels.py` compares full and subset instances
at both 400 and 700 using seven hard-coded synthetic strings only. It does not
accept user text, media or projects. It requires the exact measured source digest
and a new output directory. This probe intentionally accepts only resolved POSIX
ASCII output paths containing letters/digits and `_./-`; spaces, colons, quotes,
commas and brackets are rejected rather than incorrectly escaped through FFmpeg's
filter parser. This is a Linux-only experiment, not Windows portability coverage.
ASS is explicitly UTF-8. Font directories are separated from ASS, RGBA and logs.

```
python tools/verify/probe_caption_subset_pixels.py --source src/cutvoke/assets/fonts/NotoSansSC-VF.ttf --output-dir /tmp/new-font-pixel-probe
```

Actual Linux validation: Python 3.12.14, fontTools 4.68.0, FFmpeg 7.1.5,
libass 0.17.3, FriBidi 1.0.16 and HarfBuzz-ng 10.2.0 (FFmpeg's shaper, not the
uharfbuzz wheel's bundled version). Both 1280x720 RGBA frames at 32px with 2px
outline were byte-identical, zero changed bytes. Complete/subset generation:
400 = 12.338/1.087s, 700 = 12.799/1.288s; these timings include save/rename work.
Frame SHA256:
- 400: 2987a373ea438da3a28bac6b01242f3beaf76baa9e9538c7127a6ed362ff0fcc
- 700: 2ebe4564b0da444db16433bd357c930b4435c296482a1bf90b12aecd3f4fb67e

Inspected fontselect logs confirm CutVokePixelProbe selection and actual system
fallback to Noto Arabic, Devanagari and CJK for uncovered characters, for both
full and subset. Logs remain local and contain synthetic ASS strings/font paths;
do not feed user text into this diagnostic or upload user projects/logs. The
script fails on FFmpeg errors, wrong source, unexpected frame length or pixel
mismatch, including under optimized Python. It also rejects all-black RGB frames
and logs lacking the exact experimental font selection, preventing fallback-only
passes. This confirms selection, not per-glyph attribution; inspect logs/frames
when reproducing. Nonblack red-channel counts were 7,150/9,388 for 400/700. Other OS/font fallback installations can produce different hashes.

This improves the earlier shaping-only evidence but is NOT the production
caption compiler/cache, browser 45-second acceptance, all sizes/rotations, hinting
equivalence across rasterizers or Windows/macOS validation. The experimental
rename is only for isolated selection, not an approved production alias/cache
strategy. No resource architecture or production repair is adopted.

Safety gate self-tests: `python tools/verify/probe_caption_subset_pixels.py --self-test`
and the same command with `python -O` both passed one positive and eight negative
cases (black frame, fallback-only log, wrong byte length, five unsafe paths).
After adding these gates, the complete Linux 400/700 render comparison was rerun
successfully with the same frame hashes. No Windows/macOS or production acceptance
is implied by these probe checks.
