/** 片段右键菜单：split / remove / speed / effect.add。受控弹层。 */

import type { Dispatch } from "react";
import { Scissors, Trash2, Gauge, Wand2, Copy } from "lucide-react";
import type { Clip } from "../types/api";
import { getLatestState } from "../store/actions";
import { addEffect, duplicateClip, removeClip, setClipSpeed, splitClip } from "../store/clipEdit";
import type { EditorAction } from "../store/editor";
import { rationalToSecs, secsToRational } from "../lib/rational";
import { sourceBasename } from "../lib/media";
import { BUILTIN_TRANSFORM_ID, BUILTIN_COLOR_ID, BUILTIN_CROSSFADE_ID } from "../lib/effects";

export interface CtxTarget {
  x: number;
  y: number;
  clipId: string;
  clip: Clip | null;
  trackId: string;
  /** 播放头时间（秒）：分割默认点。 */
  playheadSecs?: number;
}

export function ClipContextMenu({
  target,
  onClose,
  dispatch,
}: {
  target: CtxTarget;
  onClose: () => void;
  dispatch: Dispatch<EditorAction>;
}) {
  const st = getLatestState();
  if (!st || !st.currentId) return null;
  const ctx = st;
  const clip = target.clip;

  const run = (fn: () => Promise<unknown>) => {
    onClose();
    void fn();
  };

  const handleSplit = () => {
    if (!clip) return;
    let at: number;
    const playhead = target.playheadSecs;
    const cs = rationalToSecs(clip.timelineStart);
    const ce = rationalToSecs(clip.timelineEnd);
    if (playhead != null && playhead > cs + 0.01 && playhead < ce - 0.01) {
      at = playhead;
    } else {
      at = (cs + ce) / 2;
    }
    run(() => splitClip(dispatch, ctx, { clipId: clip.id, time: secsToRational(at) }));
  };

  const handleRemove = () => {
    if (!clip) return;
    run(() => removeClip(dispatch, ctx, clip.id));
  };

  const handleDuplicate = () => {
    if (!clip) return;
    run(() => duplicateClip(dispatch, ctx, { clipId: clip.id, trackId: target.trackId }));
  };

  const handleSpeed = (speed: number) => {
    if (!clip) return;
    run(() => setClipSpeed(dispatch, ctx, { clipId: clip.id, speed }));
  };

  const handleEffect = (effectId: string) => {
    if (!clip) return;
    run(() =>
      addEffect(dispatch, ctx, {
        clipId: clip.id,
        effectId,
        params: defaultParamsFor(effectId),
      }),
    );
  };

  return (
    <>
      <div
        className="ctx-menu__backdrop"
        onClick={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
      />
      <div className="ctx-menu" style={{ left: target.x, top: target.y }}>
        <div className="ctx-menu__label">
          {clip ? sourceBasename(clip.assetRef.sourcePath) : "片段"}
        </div>
        <button className="ctx-menu__item" onClick={handleSplit}>
          <Scissors size={14} /> 在播放头分割
        </button>
        <button className="ctx-menu__item" onClick={handleDuplicate}>
          <Copy size={14} /> 复制片段
        </button>
        <button className="ctx-menu__item ctx-menu__item--danger" onClick={handleRemove}>
          <Trash2 size={14} /> 删除片段
        </button>
        <div className="ctx-menu__sep" />
        <div className="ctx-menu__label">速度</div>
        <button className="ctx-menu__item" onClick={() => handleSpeed(0.5)}><Gauge size={14} /> 0.5x</button>
        <button className="ctx-menu__item" onClick={() => handleSpeed(1)}><Gauge size={14} /> 1.0x</button>
        <button className="ctx-menu__item" onClick={() => handleSpeed(1.5)}><Gauge size={14} /> 1.5x</button>
        <button className="ctx-menu__item" onClick={() => handleSpeed(2)}><Gauge size={14} /> 2.0x</button>
        <button className="ctx-menu__item" onClick={() => handleSpeed(-1)}><Gauge size={14} /> 倒放 (-1x)</button>
        <div className="ctx-menu__sep" />
        <div className="ctx-menu__label">效果</div>
        <button className="ctx-menu__item" onClick={() => handleEffect(BUILTIN_TRANSFORM_ID)}>
          <Wand2 size={14} /> 画面变换
        </button>
        <button className="ctx-menu__item" onClick={() => handleEffect(BUILTIN_COLOR_ID)}>
          <Wand2 size={14} /> 基础调色
        </button>
        <button className="ctx-menu__item" onClick={() => handleEffect(BUILTIN_CROSSFADE_ID)}>
          <Wand2 size={14} /> 叠化转场
        </button>
      </div>
    </>
  );
}

function defaultParamsFor(effectId: string): Record<string, unknown> {
  switch (effectId) {
    case BUILTIN_TRANSFORM_ID:
      return { position: { x: 0, y: 0 }, scale: 1.0, rotation: 0, opacity: 1.0 };
    case BUILTIN_COLOR_ID:
      return { brightness: 0.0, contrast: 1.0, saturation: 1.0 };
    case BUILTIN_CROSSFADE_ID:
      return { duration: 1.0 };
    default:
      return {};
  }
}