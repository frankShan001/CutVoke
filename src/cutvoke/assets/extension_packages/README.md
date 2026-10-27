# Optional component provenance

The base installer contains worker code and three small pure Python wheels. It contains no neural weights or PyTorch runtime.

- Google MediaPipe Selfie Segmenter and MagicTouch weights: Apache-2.0. Source model cards: https://developers.google.com/static/ml-kit/images/vision/selfie-segmentation/selfie-model-card.pdf and https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MagicTouch.pdf . Weights are unmodified; immutable SHA-256 values and original/backup origins are in catalogs/optional_extensions.json.
- Meta SAM2.1 weights and SAM2 code: Apache-2.0. Code revision 2b90b9f5ceec907a1c18123530e92e794ad901a4 at https://github.com/facebookresearch/sam2 . Source archive provenance is in SAM2-PROVENANCE.json. The SAM2 wheel includes LICENSE and LICENSE_cctorch. Optional compiled CUDA extensions are disabled.
- iopath 0.1.10: BSD-3-Clause, license retained inside its wheel.
- ANTLR Python runtime 4.9.3: BSD-3-Clause, upstream 4.9.3 LICENSE.txt retained in wheel metadata; RECORD regenerated. Only license metadata was added.
- CutVoke worker code: MIT; the source license is retained inside cutvoke-workers.zip.

Downloaded Python and dependency wheels retain their own upstream license files and metadata. Offline extension exports include the model license and pinned model provenance in manifest.json. Installed runtimes retain MODEL-LICENSE.txt.
