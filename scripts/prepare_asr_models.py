"""Download and load-check Faster-Whisper models into a portable cache directory."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence


MODEL_CHOICES = ("tiny", "base", "small", "medium")


def prepare_models(model_sizes: Sequence[str], model_dir: Path) -> list[str]:
    """Ensure each requested model is present and loadable from ``model_dir``."""
    from faster_whisper import WhisperModel

    model_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[str] = []
    for model_size in model_sizes:
        model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
            download_root=str(model_dir),
        )
        del model  # Release native model memory before loading the next size.
        prepared.append(model_size)
    return prepared


def main(argv: Sequence[str] | None = None) -> int:
    default_dir = os.environ.get("CUTVOKE_ASR_MODEL_DIR") or str(
        Path.home() / ".cutvoke" / "asr-models"
    )
    parser = argparse.ArgumentParser(
        description="Download and verify local Faster-Whisper models for offline use."
    )
    parser.add_argument(
        "--model", action="append", choices=MODEL_CHOICES,
        help="Model to prepare; repeat for more than one (default: base).",
    )
    parser.add_argument(
        "--output", type=Path, default=Path(default_dir),
        help="Model cache directory; defaults to CUTVOKE_ASR_MODEL_DIR or ~/.cutvoke/asr-models.",
    )
    args = parser.parse_args(argv)
    models = args.model or ["base"]
    model_dir = args.output.expanduser().resolve()
    try:
        prepared = prepare_models(models, model_dir)
    except ImportError as exc:
        print("ASR dependencies are missing; install with: uv pip install -e '.[asr]'", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Could not prepare the local ASR model: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "ready": prepared,
        "modelDirectory": str(model_dir),
        "runtimeEnvironment": {"CUTVOKE_ASR_MODEL_DIR": str(model_dir)},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
