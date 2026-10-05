from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.prepare_asr_models import main


def test_prepare_asr_models_load_checks_requested_cache(monkeypatch, tmp_path: Path, capsys):
    calls = []

    class FakeWhisperModel:
        def __init__(self, model_size, **kwargs):
            calls.append((model_size, kwargs))

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))
    model_dir = tmp_path / "portable-models"

    assert main([
        "--model", "tiny", "--model", "base", "--output", str(model_dir),
    ]) == 0

    assert [name for name, _ in calls] == ["tiny", "base"]
    assert all(options == {
        "device": "cpu", "compute_type": "int8", "download_root": str(model_dir.resolve()),
    } for _, options in calls)
    assert model_dir.is_dir()
    assert '"ready": [\n    "tiny",\n    "base"\n  ]' in capsys.readouterr().out


def test_prepare_asr_models_rejects_unknown_size_before_loading():
    with pytest.raises(SystemExit) as error:
        main(["--model", "large"])
    assert error.value.code == 2
