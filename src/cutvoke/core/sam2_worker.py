"""Isolated SAM2 video tracking worker; invoked by the local CutVoke process."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import time
import traceback


def _write_progress(path: Path, completed: int, total: int, device: str) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "completed": completed, "total": total, "device": device,
    }), encoding="utf-8")
    for attempt in range(12):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 11:
                raise
            # On Windows the parent can briefly hold the progress file open
            # while reading it; retry the atomic swap after that handle closes.
            time.sleep(0.01 * (attempt + 1))


def _load_prompt_sets(args: argparse.Namespace, frame_count: int) -> list[dict]:
    if args.prompt_sets_json:
        raw_sets = json.loads(args.prompt_sets_json)
    else:
        raw_sets = [{
            "frame": args.selection_frame,
            "points": json.loads(args.points_json or "[]"),
        }]
    if not isinstance(raw_sets, list) or not 1 <= len(raw_sets) <= 16:
        raise RuntimeError("SAM2 需要 1 到 16 组提示帧")
    prompt_sets = []
    previous_frame = -1
    for raw in raw_sets:
        if not isinstance(raw, dict):
            raise RuntimeError("SAM2 提示帧格式无效")
        frame_idx = raw.get("frame")
        points = raw.get("points")
        if (isinstance(frame_idx, bool) or not isinstance(frame_idx, int) or
                not 0 <= frame_idx < frame_count or frame_idx <= previous_frame):
            raise RuntimeError("SAM2 提示帧必须在视频范围内并按时间递增")
        if not isinstance(points, list) or not 1 <= len(points) <= 8:
            raise RuntimeError("每组 SAM2 提示点必须为 1 到 8 个")
        normalized = []
        for point in points:
            if isinstance(point, dict):
                point = [point.get("x"), point.get("y"), point.get("label")]
            if (not isinstance(point, (list, tuple)) or len(point) != 3 or
                    any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                        not math.isfinite(value) or not 0 <= value <= 1
                        for value in point[:2]) or
                    isinstance(point[2], bool) or point[2] not in (0, 1)):
                raise RuntimeError("SAM2 提示点必须是归一化坐标，标签为正向 1 或排除 0")
            normalized.append([float(point[0]), float(point[1]), int(point[2])])
        if not any(point[2] == 1 for point in normalized):
            raise RuntimeError("每组 SAM2 提示至少需要一个正向人物点")
        prompt_sets.append({"frame": frame_idx, "points": normalized})
        previous_frame = frame_idx
    return prompt_sets


def _propagate_in_both_directions(predictor, state, selection_frame: int,
                                  frame_count: int):
    """Yield one mask result per frame, expanding from the first prompt both ways."""
    seen_frames = set()
    directions = (
        (False, max(0, frame_count - selection_frame - 1)),
        (True, selection_frame),
    )
    for reverse, distance in directions:
        if reverse and distance == 0:
            continue
        for result in predictor.propagate_in_video(
                state, start_frame_idx=selection_frame,
                max_frame_num_to_track=distance, reverse=reverse):
            frame_idx = int(result[0])
            if frame_idx in seen_frames:
                continue
            seen_frames.add(frame_idx)
            yield result
    missing = set(range(frame_count)) - seen_frames
    if missing:
        first_missing = min(missing)
        raise RuntimeError(f"SAM2 未生成第 {first_missing} 帧的目标遮罩")


def run(args: argparse.Namespace) -> int:
    import numpy as np
    import torch
    from PIL import Image
    from sam2.build_sam import build_sam2_video_predictor

    frames_dir = Path(args.frames_dir).resolve()
    masks_dir = Path(args.masks_dir).resolve()
    checkpoint = Path(args.checkpoint).resolve()
    progress_path = Path(args.progress_file).resolve()
    frame_paths = sorted(frames_dir.glob("*.jpg"))
    if not frame_paths or not checkpoint.is_file():
        raise RuntimeError("SAM2 输入帧或权重文件缺失")
    prompt_sets = _load_prompt_sets(args, len(frame_paths))

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
    predictor = build_sam2_video_predictor(
        "configs/sam2.1/sam2.1_hiera_b+.yaml", str(checkpoint),
        device=device, apply_postprocessing=False,
    )
    state = predictor.init_state(
        video_path=str(frames_dir), offload_video_to_cpu=True,
        offload_state_to_cpu=True,
    )
    video_width, video_height = state["video_width"], state["video_height"]
    selection_frame = prompt_sets[0]["frame"]
    total = len(frame_paths)
    _write_progress(progress_path, 0, total, device)

    with torch.inference_mode():
        for prompt_set in prompt_sets:
            points = np.asarray([
                [point[0] * video_width, point[1] * video_height]
                for point in prompt_set["points"]
            ], dtype=np.float32)
            labels = np.asarray(
                [point[2] for point in prompt_set["points"]], dtype=np.int32)
            predictor.add_new_points_or_box(
                state, frame_idx=prompt_set["frame"], obj_id=1,
                points=points, labels=labels, normalize_coords=True,
            )
        completed = 0
        context = (torch.autocast(device_type="cuda", dtype=torch.bfloat16)
                   if device == "cuda" else torch.autocast(device_type="cpu", enabled=False))
        with context:
            for frame_idx, object_ids, mask_logits in _propagate_in_both_directions(
                    predictor, state, selection_frame, len(frame_paths)):
                ids = [int(value) for value in object_ids]
                if 1 not in ids:
                    raise RuntimeError(f"SAM2 在第 {frame_idx} 帧未返回目标身份")
                mask = mask_logits[ids.index(1)]
                while mask.ndim > 2:
                    mask = mask.squeeze(0)
                binary = (mask > 0).to(torch.uint8).cpu().numpy() * 255
                if binary.shape != (video_height, video_width):
                    binary = np.asarray(Image.fromarray(binary).resize(
                        (video_width, video_height), Image.Resampling.BILINEAR))
                Image.fromarray(binary, mode="L").save(
                    masks_dir / f"{frame_idx:08d}.png")
                completed += 1
                if completed % 3 == 0 or completed == total:
                    _write_progress(progress_path, completed, total, device)
    predictor.reset_state(state)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--masks-dir", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--selection-frame", type=int)
    parser.add_argument("--points-json")
    parser.add_argument("--prompt-sets-json")
    parser.add_argument("--progress-file", required=True)
    args = parser.parse_args()
    try:
        if not args.prompt_sets_json and (args.selection_frame is None or not args.points_json):
            parser.error("provide either --prompt-sets-json or both legacy prompt arguments")
        return run(args)
    except Exception:  # noqa: BLE001 — send traceback to parent log for diagnosis
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
