"""PR builds retain the same local integrity seal as main builds."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


spec = importlib.util.spec_from_file_location(
    "seal_macos_app", Path(__file__).resolve().parents[2] / "tools/build/seal_macos_app.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_pull_request_seals_new_app_without_credentials_and_keeps_strict_verification(tmp_path):
    app = tmp_path / "CutVoke.app"
    executable = app / "Contents/MacOS/CutVoke"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"new build")
    with patch.dict("os.environ", {"GITHUB_EVENT_NAME": "pull_request"}), patch.object(module.subprocess, "run") as run:
        module.seal_app(app)
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)],
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)],
    ]
    assert all(call.kwargs["check"] for call in run.call_args_list)


def test_missing_app_is_rejected_before_any_signing(tmp_path):
    with patch.object(module.subprocess, "run") as run, pytest.raises(RuntimeError, match="Missing newly built"):
        module.seal_app(tmp_path / "CutVoke.app")
    run.assert_not_called()
