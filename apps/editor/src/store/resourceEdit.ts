import type { Dispatch } from "react";
import { msg } from "../i18n";
import type { EffectSpec } from "../lib/effects";
import { clipAcceptsEffect, type EffectDragResource } from "../lib/effectDrag";
import { rationalToSecs } from "../lib/rational";
import type { EditorAction, EditorState } from "./editor";
import { getLatestState, runCommand, type ActionResult } from "./actions";
import { applyAnimation, applyTransition } from "./clipEdit";
import { addEffectToClip } from "./effectEdit";

/** Drop and keyboard application share the same validation and undoable commands. */
export async function applyResourceToClip(dispatch: Dispatch<EditorAction>, state: EditorState,
  resource: EffectDragResource, effect: EffectSpec, clipId: string): Promise<ActionResult> {
  const current = getLatestState() || state;
  const track = current.project?.sequence.tracks.find(t => t.clips.some(c => c.id === clipId));
  const clip = track?.clips.find(c => c.id === clipId);
  const reject = (text: ReturnType<typeof msg>) => {
    dispatch({ type: "STATUS_SET", severity: "warn", text });
    return { ok: false };
  };
  if (!track || !clip) return reject(msg("请先在时间线选中一个片段"));
  if (current.editLock) return reject(msg("Agent 正在编辑，暂不可应用效果"));
  if (track.locked) return reject(msg("所选片段所在轨道已锁定，解锁后可应用效果"));
  if (resource.effectId !== effect.effectId || !clipAcceptsEffect(effect, track, clip)
    || !clipAcceptsEffect(resource, track, clip)) return reject(msg("不适用于当前片段"));
  let duration = typeof resource.duration === "number" && Number.isFinite(resource.duration) ? resource.duration : 1;
  if (effect.category === "transition") {
    if (track.kind !== "video") return reject(msg("转场只能用于视频轨道"));
    const ordered = [...track.clips].sort((a,b) => rationalToSecs(a.timelineStart) - rationalToSecs(b.timelineStart));
    const previous = ordered[ordered.findIndex(c => c.id === clipId) - 1];
    if (!previous) return reject(msg("首个片段前没有可连接的片段"));
    if (Math.abs(rationalToSecs(clip.timelineStart) - rationalToSecs(previous.timelineEnd)) > .001)
      return reject(msg("两段之间有空隙，先贴合片段再应用转场"));
    const max = Math.min(5, (rationalToSecs(previous.timelineEnd) - rationalToSecs(previous.timelineStart))/2,
      (rationalToSecs(clip.timelineEnd) - rationalToSecs(clip.timelineStart))/2);
    if (max < .1) return reject(msg("相邻片段过短，无法形成转场"));
    duration = Math.round(Math.min(max, Math.max(.1, duration))*1000)/1000;
  }
  const result = resource.presetId
    ? await runCommand(dispatch, current, "builtinPreset.apply", { clipId, presetId: resource.presetId,
        ...(effect.category === "transition" ? { duration } : {}) })
    : effect.category === "transition" ? await applyTransition(dispatch, current, { clipId, effectId: effect.effectId, duration })
    : effect.category === "animation" ? await applyAnimation(dispatch, current, {
        clipId, animationId: effect.effectId, duration, params: effect.defaults })
    : await addEffectToClip(dispatch, current, { clipId, effectId: effect.effectId });
  if (result.ok && (getLatestState() || current).currentId === current.currentId) {
    dispatch({ type: "SELECTION_SET", selection: { trackId: track.id, clipId } });
    dispatch({ type: "PLAYHEAD_SET", t: rationalToSecs(clip.timelineStart) });
    dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已应用「{0}」到选中片段", effect.name) });
    window.dispatchEvent(new CustomEvent("cutvoke:edit-effect", { detail: {
      effectId: effect.effectId, category: effect.category, trackId: track.id, clipId,
    } }));
  }
  return result;
}
