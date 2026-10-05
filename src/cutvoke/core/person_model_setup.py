"""Download and verify the optional local person-segmentation model."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from .person_cutout import (
    INTERACTIVE_MODEL_SHA256, INTERACTIVE_MODEL_URL, MODEL_SHA256, MODEL_URL,
    SAM2_GIT_REVISION, SAM2_MODEL_SHA256, SAM2_MODEL_URL, SAM2_RUNTIME_MARKER,
    _file_has_sha256, _interactive_model_is_valid, _model_is_valid,
    interactive_model_path, model_path, sam2_model_path, sam2_runtime_is_ready,
    sam2_runtime_python, sam2_runtime_root,
)


MAX_MODEL_BYTES = 8 * 1024 * 1024
MAX_SAM2_MODEL_BYTES = 1024 * 1024 * 1024


def prepare_model(destination: Path, *, interactive: bool = False, opener=urlopen) -> dict:
    model_url = INTERACTIVE_MODEL_URL if interactive else MODEL_URL
    model_sha256 = INTERACTIVE_MODEL_SHA256 if interactive else MODEL_SHA256
    model_label = "MagicTouch 点提示模型" if interactive else "人物分割模型"
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(model_url, headers={"User-Agent": "CutVoke local person cutout"})
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as output, opener(request, timeout=60) as response:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_MODEL_BYTES:
                    raise ValueError("下载的模型超过 8 MiB 上限")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size <= 0:
            raise ValueError("下载的模型文件为空")
        if digest.hexdigest() != model_sha256:
            raise ValueError(
                f"{model_label} SHA-256 校验失败：{digest.hexdigest()}")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {"path": str(destination), "sizeBytes": size,
            "sha256": digest.hexdigest()}


def prepare_sam2_model(destination: Path, *, opener=urlopen) -> dict:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(SAM2_MODEL_URL, headers={"User-Agent": "CutVoke local person cutout"})
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as output, opener(request, timeout=120) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_SAM2_MODEL_BYTES:
                    raise ValueError("SAM2 权重超过 1 GiB 上限")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size <= 0 or digest.hexdigest() != SAM2_MODEL_SHA256:
            raise ValueError("SAM2 权重 SHA-256 校验失败")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {"path": str(destination), "sizeBytes": size,
            "sha256": digest.hexdigest()}


def _run_install_step(command: list[str], *, env: dict[str, str], label: str) -> None:
    print(label, flush=True)
    subprocess.run(command, check=True, env=env)


def prepare_sam2_runtime(*, check_only: bool = False) -> dict:
    root = sam2_runtime_root()
    python = sam2_runtime_python()
    marker_path = root / SAM2_RUNTIME_MARKER
    if check_only:
        if not sam2_runtime_is_ready():
            raise ValueError(f"SAM2 隔离运行时未准备或版本不匹配：{python}")
        result = subprocess.run(
            [str(python), "-c", "import torch; print(torch.__version__); print(torch.cuda.is_available())"],
            check=True, capture_output=True, text=True, timeout=120)
        lines = result.stdout.strip().splitlines()
        return {"python": str(python), "torch": lines[0] if lines else "",
                "cudaAvailable": len(lines) > 1 and lines[1].strip().lower() == "true"}

    root.parent.mkdir(parents=True, exist_ok=True)
    if not python.is_file():
        _run_install_step(
            [sys.executable, "-m", "venv", str(root)], env=os.environ.copy(),
            label=f"创建隔离 SAM2 环境：{root}")
    env = os.environ.copy()
    env["SAM2_BUILD_CUDA"] = "0"
    test_import = subprocess.run(
        [str(python), "-c", "import torch; import sam2; print(torch.__version__)"],
        check=False, capture_output=True, text=True, timeout=120)
    if test_import.returncode:
        _run_install_step(
            [str(python), "-m", "pip", "install", "--upgrade", "pip"],
            env=env, label="更新隔离环境中的 pip")
        if platform.system() == "Windows" or shutil.which("nvidia-smi"):
            _run_install_step(
                [str(python), "-m", "pip", "install", "torch", "torchvision",
                 "--index-url", "https://download.pytorch.org/whl/cu128"],
                env=env, label="安装 PyTorch CUDA 12.8")
        else:
            _run_install_step(
                [str(python), "-m", "pip", "install", "torch", "torchvision"],
                env=env, label="安装 PyTorch")
        _run_install_step(
            [str(python), "-m", "pip", "install",
             f"git+https://github.com/facebookresearch/sam2.git@{SAM2_GIT_REVISION}"],
            env=env, label="安装 Meta SAM2 视频追踪器")
    result = subprocess.run(
        [str(python), "-c", "import torch; import sam2; print(torch.__version__); print(torch.cuda.is_available())"],
        check=True, capture_output=True, text=True, timeout=120)
    lines = result.stdout.strip().splitlines()
    marker_path.write_text(json.dumps({
        "revision": SAM2_GIT_REVISION,
        "torch": lines[0] if lines else "",
        "cudaAvailable": len(lines) > 1 and lines[1].strip().lower() == "true",
    }, indent=2), encoding="utf-8")
    return {"python": str(python), "torch": lines[0] if lines else "",
            "cudaAvailable": len(lines) > 1 and lines[1].strip().lower() == "true"}


def dependency_status() -> dict[str, bool]:
    return {
        "mediapipe": importlib.util.find_spec("mediapipe") is not None,
        "opencv": importlib.util.find_spec("cv2") is not None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interactive", action="store_true",
                        help="prepare the optional MagicTouch point-prompt model")
    parser.add_argument("--sam2", action="store_true",
                        help="install an isolated SAM2 video-tracking runtime and download its model")
    parser.add_argument("--output", type=Path,
                        help="model path (defaults to the selected model's ~/.cutvoke/models path)")
    parser.add_argument("--check", action="store_true",
                        help="verify the local model and optional runtime without downloading")
    args = parser.parse_args(argv)
    if args.interactive and args.sam2:
        parser.error("--interactive and --sam2 are separate model setup actions")
    if args.sam2:
        try:
            runtime = prepare_sam2_runtime(check_only=args.check)
            if args.check:
                if not _file_has_sha256(sam2_model_path(), SAM2_MODEL_SHA256):
                    print(f"SAM2 权重缺失或校验失败：{sam2_model_path()}", file=sys.stderr)
                    return 1
                result = {"path": str(sam2_model_path()), "sha256": SAM2_MODEL_SHA256}
            else:
                result = ({"path": str(sam2_model_path()), "sha256": SAM2_MODEL_SHA256}
                          if _file_has_sha256(sam2_model_path(), SAM2_MODEL_SHA256)
                          else prepare_sam2_model(sam2_model_path()))
        except Exception as error:  # noqa: BLE001 — show actionable CLI failure
            print(f"SAM2 视频追踪准备失败：{error}", file=sys.stderr)
            return 1
        print(f"SAM2 视频追踪已就绪：{result['path']}")
        print(f"SHA-256：{result['sha256']}")
        print(f"隔离运行时：{runtime['python']}（PyTorch {runtime['torch']}，CUDA {runtime['cudaAvailable']}）")
        return 0
    destination = args.output or (interactive_model_path() if args.interactive else model_path())
    runtime = dependency_status()
    if not all(runtime.values()):
        print("缺少可选依赖，请先运行：pip install cutvoke[person]", file=sys.stderr)
        return 2
    try:
        if args.check:
            validator = _interactive_model_is_valid if args.interactive else _model_is_valid
            expected_digest = (INTERACTIVE_MODEL_SHA256 if args.interactive
                               else MODEL_SHA256)
            if not validator(destination.expanduser().resolve()):
                print(f"模型缺失或校验失败：{destination}", file=sys.stderr)
                return 1
            result = {"path": str(destination.expanduser().resolve()),
                      "sha256": expected_digest}
        else:
            result = prepare_model(destination, interactive=args.interactive)
    except Exception as error:  # noqa: BLE001 — show actionable CLI failure
        print(f"人物分割模型准备失败：{error}", file=sys.stderr)
        return 1
    print(f"{'MagicTouch 点提示模型' if args.interactive else '人物分割模型'}已就绪：{result['path']}")
    print(f"SHA-256：{result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
