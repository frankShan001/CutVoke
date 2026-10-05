# SAM2 video identity tracking for person cutout

## Product behavior

Selected-person cutout now offers three tracking modes:

- **Contour tracking** keeps the existing lightweight MediaPipe path.
- **MagicTouch** keeps the existing per-frame point-prompt path.
- **SAM2 video identity tracking** uses a pinned Meta SAM2 video predictor in a separate Python environment. Click the target person for a positive point. Shift-click other people to add exclusion points; add more positive points on the same person when the first prompt does not cover the full body. The result remains a transparent ProRes 4444 clip with source audio preserved.

SAM2 dependencies and weights are not bundled with the default installation. On a CUDA 12.8 Windows machine, the setup command creates a user-level environment, installs PyTorch and SAM2, and downloads the 323 MB SAM2.1 Hiera Base+ checkpoint:

```powershell
uv run cutvoke-person-model --sam2
```

The regular person extra still provides the OpenCV decoder used to prepare frames:

```powershell
uv sync --extra person
```

To verify the setup without downloading again:

```powershell
uv run cutvoke-person-model --sam2 --check
```

The runtime is pinned to SAM2 source revision `2b90b9f5ceec907a1c18123530e92e794ad901a4`; the checkpoint is verified against SHA-256 `a2345aede8715ab1d5d31b4a509fb160c5a4af1970f199d9054ccfb746c004c5`. The CUDA extension is disabled, so the predictor uses its regular mask output without the extension's small-hole/speckle cleanup. Frames are resized to at most 1024 pixels on the long edge for inference, then masks are resized to source dimensions.

## Acceptance evidence

`output/acceptance/jy-r20-person-sam2-20260926/acceptance.json` records the runtime, model, source files, generated outputs, frame counts, and manual contact-sheet review.

- A five-second 1280×720 crosswalk sample processed 150/150 frames with no reported mask-loss frames. The contact sheet shows a stable selected-person silhouette across the clip.
- A five-second dense Shibuya-crossing sample processed 125/125 frames. A second prompt variant also remained available for the full-clip run.
- The full 15-second dense Shibuya sample processed 375 frames but selected-person pixels were present in only 200; 175 frames were transparent after tracking loss. This is a real processing result, not an acceptance pass for continuous identity through dense occlusion.
- Python pytest: **201 passed, 500 subtests passed**. Full `npm run test:e2e`, including TypeScript checks and the production build: **55 passed**.

## Current limits

- The current picker places prompts on one frame. SAM2 can follow the selected identity through ordinary motion, but dense crossings and long occlusions can still lose it. Lost frames stay transparent and are shown in the job summary so another person is not silently substituted.
- Short crosswalk success does not certify natural camera footage, hair-edge quality, all crowd densities, or long-video memory/runtime use. The 15-second Shibuya result is evidence that continuous dense-crowd tracking remains partial.
- Native Windows inference succeeded on this GeForce RTX 5090 Laptop GPU with PyTorch `2.11.0+cu128`. Meta recommends WSL with Ubuntu for Windows installs; other GPU, driver, and Python combinations still need testing.

The original task matrix therefore remains partial for JY-R20's identity tracking, real-footage quality, and independent-user acceptance.
