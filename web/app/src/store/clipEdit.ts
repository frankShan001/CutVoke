/** 片段级编辑动作：move / trim / split / remove / speed / effect.add / 拖放导入。
    统一走 runCommand（自动 REVISION_CONFLICT 重试 + 结构化错误）。 */

import type { Dispatch } from "react";
import type { EditorAction, EditorState } from "./editor";
import { showError } from "./editor";
import { ApiFailure } from "../lib/api";
import { getLatestState, runCommand, type ActionResult } from "./actions";
import { probeMedia } from "../lib/mediaApi";
import { rationalToSecs, secsToRational } from "../lib/rational";
import type { Rational, KeyframeInterpolation, ResourceReference } from "../types/api";

/** Dragging library media onto a clip means before/after the whole clip. */
export function resolveInsertStart(state: EditorState, trackId: string, requestedSeconds: number): Rational {
  const at = Math.max(0, requestedSeconds);
  const track = state.project?.sequence.tracks.find((item) => item.id === trackId);
  const occupied = track?.clips.find((clip) => {
    const start = rationalToSecs(clip.timelineStart);
    const end = rationalToSecs(clip.timelineEnd);
    return at > start + 1e-6 && at < end - 1e-6;
  });
  if (occupied) {
    const start = rationalToSecs(occupied.timelineStart);
    const end = rationalToSecs(occupied.timelineEnd);
    return at < (start + end) / 2 ? occupied.timelineStart : occupied.timelineEnd;
  }
  const fps = state.project?.sequence.fps;
  const rate = fps ? Number(fps.num) / Number(fps.den) : 30;
  if (Number.isFinite(rate) && rate > 0) {
    return { num: String(Math.round(at * rate) * Number(fps?.den || 1)), den: String(Number(fps?.num || 30)) };
  }
  return secsToRational(at);
}

function addRational(left: Rational, right: Rational): Rational {
  return {
    num: String(BigInt(left.num) * BigInt(right.den) + BigInt(right.num) * BigInt(left.den)),
    den: String(BigInt(left.den) * BigInt(right.den)),
  };
}

function videoAnchorAt(state: EditorState, seconds: number): string | undefined {
  const selected = state.selection?.clipId;
  const candidates = (state.project?.sequence.tracks || [])
    .filter((track) => track.kind === "video" && track.role !== "sticker")
    .flatMap((track) => track.clips)
    .filter((clip) => rationalToSecs(clip.timelineStart) <= seconds + 1e-6
      && seconds < rationalToSecs(clip.timelineEnd) - 1e-6);
  return candidates.find((clip) => clip.id === selected)?.id || candidates[0]?.id;
}

/** 素材拖入时间线：探测真实时长（失败按 2s），始终在完整片段边界插入。 */
export async function insertClipFromDrop(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { trackId: string; sourcePath: string; assetId?: string; timelineStartSecs: number;
    createTrackKind?: "video" | "audio"; createTrackRole?: "sticker"; role?: "sticker";
    stickerAnimation?: { effectId: string; params: Record<string, unknown> };
    resourceRef?: ResourceReference; stickerScale?: number },
): Promise<ActionResult> {
  let dur = 2;
  const probe = await probeMedia(input.sourcePath);
  if (probe.kind === "ok" && probe.data.duration > 0) {
    dur = probe.data.duration;
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已探测素材 ${dur.toFixed(1)}s` });
  } else {
    dispatch({ type: "STATUS_SET", severity: "warn", text: "未能探测素材时长，按 2s 导入（可稍后 trim）" });
  }
  const current = getLatestState() || state;
  const start = resolveInsertStart(current, input.trackId, input.timelineStartSecs);
  const fps = current.project?.sequence.fps;
  const fpsNum = Number(fps?.num || 30);
  const fpsDen = Number(fps?.den || 1);
  const frames = Math.max(1, Math.floor(dur * fpsNum / fpsDen + 1e-6));
  const duration: Rational = { num: String(frames * fpsDen), den: String(fpsNum) };
  const clipId = `clip_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  const result = await runCommand(dispatch, current, "clip.insert", {
    clipId,
    trackId: input.trackId,
    ...(input.createTrackKind ? { createTrackKind: input.createTrackKind } : {}),
    ...(input.createTrackRole ? { createTrackRole: input.createTrackRole } : {}),
    ...(input.role ? { role: input.role } : {}),
    ...(input.role === "sticker" && videoAnchorAt(current, rationalToSecs(start))
      ? { attachedToClipId: videoAnchorAt(current, rationalToSecs(start)) } : {}),
    ...(input.stickerAnimation ? { stickerAnimation: input.stickerAnimation } : {}),
    ...(input.resourceRef ? { resourceRef: input.resourceRef } : {}),
    ...(input.stickerScale !== undefined ? { stickerScale: input.stickerScale } : {}),
    ...(input.assetId ? { assetId: input.assetId } : {}),
    sourcePath: input.sourcePath,
    sourceStart: secsToRational(0),
    timelineStart: start,
    timelineEnd: addRational(start, duration),
    // 后续片段整体右移；落点先对齐边界，因此不会切开鼠标下的片段。
    mode: "insert",
  });
  if (result.ok) {
    const changes = result.command?.changedEntities || [];
    const moved = changes.filter((change) => change.type === "clip" && change.change === "moved").length;
    const details = [
      moved ? `${moved} 个后续片段已后移` : "",
    ].filter(Boolean);
    dispatch({
      type: "STATUS_SET",
      severity: "ok",
      text: details.length ? `已插入素材，${details.join("，")}` : "已插入素材",
    });
  }
  return result;
}

/** 素材拖到轨道区空白 → 按素材类型自动新建轨道并插入（剪映自动建轨交互）。 */
export async function insertClipAutoTrack(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { sourcePath: string; assetId?: string; trackKind: "video" | "audio";
    trackRole?: "sticker"; timelineStartSecs?: number;
    stickerAnimation?: { effectId: string; params: Record<string, unknown> };
    resourceRef?: ResourceReference; stickerScale?: number },
): Promise<ActionResult> {
  const trackId = `track_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  return insertClipFromDrop(dispatch, state, {
    trackId,
    sourcePath: input.sourcePath,
    assetId: input.assetId,
    timelineStartSecs: input.timelineStartSecs ?? 0,
    createTrackKind: input.trackKind,
    createTrackRole: input.trackRole,
    role: input.trackRole,
    stickerAnimation: input.stickerAnimation,
    resourceRef: input.resourceRef,
    stickerScale: input.stickerScale,
  });
}

/** 文字标题使用独立 text 轨与 cutvoke.text 效果，原子插入且可一次撤销。 */
export async function insertTitleAutoTrack(
  dispatch: Dispatch<EditorAction>, state: EditorState,
  input: { text: Record<string, unknown>; presetId?: string; timelineStartSecs: number; durationSecs?: number },
): Promise<ActionResult> {
  const current = getLatestState() || state;
  const start = resolveInsertStart(current, "", input.timelineStartSecs);
  const end = addRational(start, secsToRational(input.durationSecs ?? 3));
  return runCommand(dispatch, current, "clip.insert", {
    trackId: `title_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
    createTrackKind: "text",
    text: input.text,
    ...(input.presetId ? { textPresetId: input.presetId } : {}),
    ...(videoAnchorAt(current, rationalToSecs(start))
      ? { attachedToClipId: videoAnchorAt(current, rationalToSecs(start)) } : {}),
    timelineStart: start,
    timelineEnd: end,
  });
}

/** 移动片段：{clipId, timelineStart?, trackId?}。
    同轨移动只给 timelineStart；跨轨给 trackId（目标视频轨）+ timelineStart，
    一次提交完成"换轨 + 改位置"。后端对 kind 不一致/重叠返回 400。 */
export async function moveClip(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: {
    clipId: string;
    timelineStart?: Rational;
    trackId?: string;
    mode?: "reorder";
    anchorClipId?: string;
    anchorPosition?: "before" | "after";
    followAttachments?: boolean;
  },
): Promise<ActionResult> {
  const payload: Record<string, unknown> = { clipId: input.clipId };
  if (input.timelineStart) payload.timelineStart = input.timelineStart;
  if (input.trackId) payload.trackId = input.trackId;
  if (input.mode) payload.mode = input.mode;
  if (input.anchorClipId) payload.anchorClipId = input.anchorClipId;
  if (input.anchorPosition) payload.anchorPosition = input.anchorPosition;
  if (input.followAttachments !== undefined) payload.followAttachments = input.followAttachments;
  const res = await runCommand(dispatch, state, "clip.move", payload);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    const attached = (res.command?.changedEntities || [])
      .filter((item) => item.type === "clip" && item.reason === "attached").length;
    dispatch({ type: "STATUS_SET", severity: "ok", text:
      `${input.mode === "reorder" ? "已调整片段顺序" : "已移动片段"}${attached ? `，${attached} 个关联片段同步移动` : ""}` });
  }
  return res;
}

/** 关闭指定片段前的一处空隙；后续片段整体平移，保留其它空隙。 */
export async function closeGapBeforeClip(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { trackId: string; beforeClipId: string },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "clip.closeGap", input);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已关闭此处空隙，后续片段随之左移" });
  }
  return res;
}

/** 删除轨道：{trackId}。仅空轨可删；非空 400 → 前端提示先删片段。 */
export async function removeTrack(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  trackId: string,
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "track.remove", { trackId });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已删除轨道 ${trackId}` });
  }
  return res;
}

/** 更新轨道属性（锁定/静音/显隐）：{trackId, locked?, muted?, visible?}（缺省字段不变）。 */
export async function updateTrack(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { trackId: string; locked?: boolean; muted?: boolean; visible?: boolean },
): Promise<ActionResult> {
  const payload: Record<string, unknown> = { trackId: input.trackId };
  if (input.locked !== undefined) payload.locked = input.locked;
  if (input.muted !== undefined) payload.muted = input.muted;
  if (input.visible !== undefined) payload.visible = input.visible;
  const res = await runCommand(dispatch, state, "track.update", payload);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
  }
  return res;
}

/** 添加字幕：{captionId?, text, start, end}。时间必须为有理数。 */
export async function addCaption(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { captionId?: string; text: string; start: Rational; end: Rational },
): Promise<ActionResult> {
  const payload: Record<string, unknown> = {
    text: input.text,
    start: input.start,
    end: input.end,
  };
  if (input.captionId) payload.captionId = input.captionId;
  const res = await runCommand(dispatch, state, "caption.add", payload);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已添加字幕" });
  }
  return res;
}

/** 删除字幕：{captionId}。 */
export async function removeCaption(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  captionId: string,
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "caption.remove", { captionId });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已删除字幕" });
  }
  return res;
}

/** 更新字幕：{captionId, text?, start?, end?, fontSize?, color?, strokeColor?, strokeWidth?, background?, align?, bold?, animIn?, animOut?, x?, y?, scale?, rotation?}。
    仅传需要变更的字段（缺省字段不变），走 caption.update 命令。 */
export async function updateCaption(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: {
    captionId: string;
    text?: string;
    start?: Rational;
    end?: Rational;
    words?: { text: string; start: Rational; end: Rational }[];
    fontSize?: number;
    fontFamily?: "Noto Sans SC" | "Noto Serif SC";
    color?: string;
    wordHighlightColor?: string;
    strokeColor?: string;
    strokeWidth?: number;
    background?: string;
    align?: "left" | "center" | "right";
    bold?: boolean;
    animIn?: number;
    animOut?: number;
    animInStyle?: "fade" | "scale" | "typewriter" | "none";
    animLoopStyle?: "none" | "pulse" | "blink";
    animLoopMs?: number;
    /** 画布几何（H01）：x 归一化 0~1。 */
    x?: number;
    /** 画布几何（H01）：y 归一化 0~1。 */
    y?: number;
    /** 画布几何（H01）：缩放 0.1~5。 */
    scale?: number;
    /** 画布几何（H01）：旋转 -180~180。 */
    rotation?: number;
  },
): Promise<ActionResult> {
  const payload: Record<string, unknown> = { captionId: input.captionId };
  if (input.text !== undefined) payload.text = input.text;
  if (input.start) payload.start = input.start;
  if (input.end) payload.end = input.end;
  if (input.words !== undefined) payload.words = input.words;
  if (input.fontSize !== undefined) payload.fontSize = input.fontSize;
  if (input.fontFamily !== undefined) payload.fontFamily = input.fontFamily;
  if (input.color !== undefined) payload.color = input.color;
  if (input.wordHighlightColor !== undefined) payload.wordHighlightColor = input.wordHighlightColor;
  if (input.strokeColor !== undefined) payload.strokeColor = input.strokeColor;
  if (input.strokeWidth !== undefined) payload.strokeWidth = input.strokeWidth;
  if (input.background !== undefined) payload.background = input.background;
  if (input.align !== undefined) payload.align = input.align;
  if (input.bold !== undefined) payload.bold = input.bold;
  if (input.animIn !== undefined) payload.animIn = input.animIn;
  if (input.animOut !== undefined) payload.animOut = input.animOut;
  if (input.animInStyle !== undefined) payload.animInStyle = input.animInStyle;
  if (input.animLoopStyle !== undefined) payload.animLoopStyle = input.animLoopStyle;
  if (input.animLoopMs !== undefined) payload.animLoopMs = input.animLoopMs;
  if (input.x !== undefined) payload.x = input.x;
  if (input.y !== undefined) payload.y = input.y;
  if (input.scale !== undefined) payload.scale = input.scale;
  if (input.rotation !== undefined) payload.rotation = input.rotation;
  const res = await runCommand(dispatch, state, "caption.update", payload);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    const changedContent = input.text !== undefined || input.start !== undefined || input.end !== undefined;
    const changedGeometry = input.x !== undefined || input.y !== undefined
      || input.scale !== undefined || input.rotation !== undefined;
    dispatch({
      type: "STATUS_SET",
      severity: "ok",
      text: changedContent
        ? "已更新字幕文字与时间"
        : changedGeometry
          ? "已更新字幕位置与变换"
          : "已更新字幕样式",
    });
  }
  return res;
}

/** 裁剪片段：只改一端边界（{clipId, timelineStart?} 或 {clipId, timelineEnd?}）。 */
export async function trimClip(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string } & ({ newStart: Rational; newEnd?: never } | { newStart?: never; newEnd: Rational }),
): Promise<ActionResult> {
  const payload: Record<string, unknown> = { clipId: input.clipId };
  if (input.newStart) payload.timelineStart = input.newStart;
  if (input.newEnd) payload.timelineEnd = input.newEnd;
  const res = await runCommand(dispatch, state, "clip.trim", payload);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已裁剪片段" });
  }
  return res;
}

/** 分割片段：在指定时间点一分为二（payload: {clipId, at}，后端生成 clipId_r 右段）。 */
export async function splitClip(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; time: Rational },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "clip.split", { clipId: input.clipId, at: input.time });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已在游标处分割片段" });
  }
  return res;
}

/** 删除片段（payload: {clipId}）。 */
export async function removeClip(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  clipId: string,
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "clip.remove", { clipId });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已删除片段" });
  }
  return res;
}

/** 复制片段（深拷贝到同轨、紧跟源片段之后）。复制后选中新片段。 */
export async function duplicateClip(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; trackId: string },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "clip.duplicate", { clipId: input.clipId });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    // 后端生成新片段 id（newClipId），从 changedEntities 推断并选中
    const entities = res.command?.changedEntities || [];
    const added =
      entities.find((e) => e.type === "clip" && e.change === "added")?.id ??
      entities.find((e) => e.type === "clip")?.id;
    if (added) {
      dispatch({ type: "SELECTION_SET", selection: { trackId: input.trackId, clipId: added } });
    }
  }
  return res;
}

/** 调速（payload: {clipId, speed}，speed 为数字或有理数，负数倒放）。 */
export async function setClipSpeed(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; speed: number | Rational; preservePitch?: boolean;
    frameInterpolation?: "none" | "motion" },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "clip.speed", {
    clipId: input.clipId, speed: input.speed,
    ...(input.preservePitch === undefined ? {} : { preservePitch: input.preservePitch }),
    ...(input.frameInterpolation === undefined ? {} : { frameInterpolation: input.frameInterpolation }),
  });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已调整速度" });
  }
  return res;
}

/** 片段音频（音量/淡入/淡出）：{clipId, volume?, fadeIn?, fadeOut?}（缺省字段不变）。
    volume 为相对音量（1.0=原声，0=静音，可 >1）；fadeIn/fadeOut 为秒。 */
export async function setClipAudio(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; volume?: number; fadeIn?: number; fadeOut?: number },
): Promise<ActionResult> {
  const payload: Record<string, unknown> = { clipId: input.clipId };
  if (input.volume !== undefined) payload.volume = input.volume;
  if (input.fadeIn !== undefined) payload.fadeIn = input.fadeIn;
  if (input.fadeOut !== undefined) payload.fadeOut = input.fadeOut;
  const res = await runCommand(dispatch, state, "clip.audio", payload);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已更新片段音频" });
  }
  return res;
}

/** 添加片段参数关键帧：clip.keyframe action=add。time 为秒数（自动转有理数）。
    value 为数值（opacity 取 0-1）。interpolation 缺省走后端默认 linear。 */
export async function addKeyframe(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: {
    clipId: string;
    param: string;
    time: number | Rational;
    value: number;
    interpolation?: KeyframeInterpolation;
  },
): Promise<ActionResult> {
  const payload: Record<string, unknown> = {
    clipId: input.clipId,
    action: "add",
    param: input.param,
    time: typeof input.time === "number" ? secsToRational(input.time) : input.time,
    value: input.value,
  };
  if (input.interpolation) payload.interpolation = input.interpolation;
  const res = await runCommand(dispatch, state, "clip.keyframe", payload);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已添加关键帧 ${input.param}` });
  }
  return res;
}

/** 移除片段参数关键帧：clip.keyframe action=remove（按 keyframeId）。 */
export async function removeKeyframe(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; param: string; keyframeId: string },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "clip.keyframe", {
    clipId: input.clipId,
    action: "remove",
    param: input.param,
    keyframeId: input.keyframeId,
  });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已删除关键帧 ${input.param}` });
  }
  return res;
}

/** 换图套版（J02 图片完整创作核心）：只替换片段素材引用，保留 timeline 时长 /
    effects 动画 / speed / keyframes / volume / fade 全部属性。
    - 入参二选一：clipIds（精确替换指定片段数组）或 trackId（整轨替换）。
    - 新素材由 sourcePath（必填，用于渲染定位）与 assetId（可选，资产账本 id）指定。
    后端 asset.swap 命令返回 changed_entities，前端刷新工程状态后选中被替换片段。 */
export async function swapClipAsset(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipIds?: string[]; trackId?: string; sourcePath: string; assetId?: string },
): Promise<ActionResult> {
  const payload: Record<string, unknown> = {
    sourcePath: input.sourcePath,
  };
  if (input.assetId) payload.assetId = input.assetId;
  if (input.clipIds && input.clipIds.length > 0) {
    payload.clipIds = input.clipIds;
  } else if (input.trackId) {
    payload.trackId = input.trackId;
  } else {
    // 既无精确片段也无整轨：无替换目标，本地拦截（不发请求）
    const err = {
      status: 400,
      code: "NO_TARGET_CLIP",
      message: "请先在时间线选中一个片段，或指定整轨",
    };
    showError(dispatch, err);
    return { ok: false, error: err as ApiFailure };
  }
  const res = await runCommand(dispatch, state, "asset.swap", payload);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    // 精确替换时保持选中第一个被替换片段，便于继续微调
    const first = input.clipIds?.[0];
    if (first) {
      const clip = findClip(state, first);
      if (clip) {
        const trackId = (state.project?.sequence.tracks || []).find((t) =>
          (t.clips || []).some((c) => c.id === first),
        )?.id;
        if (trackId) dispatch({ type: "SELECTION_SET", selection: { trackId, clipId: first } });
      }
    }
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已换图套版：保留时长/动画/效果，仅替换素材" });
  }
  return res;
}

/** 添加效果（payload: {clipId, effectId, params}）。 */
export async function addEffect(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; effectId: string; params: Record<string, unknown> },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "effect.add", {
    clipId: input.clipId,
    effectId: input.effectId,
    params: input.params,
  });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已添加效果 ${input.effectId}` });
  }
  return res;
}

/** 移除效果（payload: {clipId, effectId}，后端删该片段上全部该 effectId 实例）。 */
export async function removeEffect(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; effectId: string },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "effect.remove", {
    clipId: input.clipId,
    effectId: input.effectId,
  });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已移除效果 ${input.effectId}` });
  }
  return res;
}

/** 原子替换转场：一次提交，失败时原转场不变。 */
export async function applyTransition(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: {
    clipId: string;
    effectId: string;
    duration: number;
  },
): Promise<ActionResult> {
  const result = await runCommand(dispatch, state, "effect.setTransition", {
    clipId: input.clipId,
    effectId: input.effectId,
    params: { duration: input.duration },
  });
  if (result.ok) dispatch({ type: "STATUS_SET", severity: "ok", text: "已应用转场" });
  return result;
}

/** Add or update several clip-local numeric keyframes in one undoable command. */
export async function addKeyframes(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: {
    clipId: string;
    keyframes: { param: string; time: number | Rational; value: number; interpolation?: KeyframeInterpolation }[];
  },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "clip.keyframe", {
    clipId: input.clipId,
    action: "batch",
    keyframes: input.keyframes.map((keyframe) => ({
      ...keyframe,
      time: typeof keyframe.time === "number" ? secsToRational(keyframe.time) : keyframe.time,
    })),
  });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已记录 ${input.keyframes.length} 个变换关键帧` });
  }
  return res;
}

/** A source-relative speed curve is one atomic edit and one undo point. */
export async function setClipCurve(
  dispatch: Dispatch<EditorAction>, state: EditorState,
  input: { clipId: string; points: { at: number; speed: number }[];
    frameInterpolation?: "none" | "motion" },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "clip.speed", {
    clipId: input.clipId, curve: { points: input.points }, preservePitch: true,
    ...(input.frameInterpolation === undefined ? {} : { frameInterpolation: input.frameInterpolation }),
  });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已应用速度曲线" });
  }
  return res;
}

/** Select how sub-frame motion is synthesized for slow-motion video clips. */
export async function setClipFrameInterpolation(
  dispatch: Dispatch<EditorAction>, state: EditorState,
  input: { clipId: string; frameInterpolation: "none" | "motion" },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "clip.speed", {
    clipId: input.clipId, frameInterpolation: input.frameInterpolation,
  });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text:
      input.frameInterpolation === "motion" ? "已启用运动估算补帧" : "已关闭慢动作补帧" });
  }
  return res;
}

/** 应用动画：后端按入场、出场、循环、组合槽位原子替换。
    animationId 为空串时清除指定槽位；params 使用注册表参数。 */
export async function applyAnimation(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: {
    clipId: string;
    animationId: string;
    duration: number;
    scale?: number;
    amplitude?: number;
    period?: number;
    /** 显式参数（J01 数据驱动）：提供时直接采用，覆盖下方按 id 推断的旧逻辑，
        以支持注册表里新增的动画（duration/easing/distance/fromAngle/overshoot/direction/amplitude/period）。 */
    params?: Record<string, unknown>;
    slot?: "入场" | "出场" | "循环" | "组合";
    keyframePolicy?: "combine" | "replace";
  },
): Promise<ActionResult> {
  const current = findClip(state, input.clipId);
  const existing = current?.effects?.filter((effect) => String(effect.effectId).startsWith("cutvoke.anim.")) || [];
  if (!input.animationId && existing.length === 0) return { ok: true };
  const id = input.animationId;
  const params: Record<string, unknown> = input.params ? { ...input.params } : {};
  if (!input.params && id === "cutvoke.anim.fadeIn") {
    params.duration = input.duration;
    params.easing = "linear";
  } else if (!input.params && id === "cutvoke.anim.zoomIn") {
    params.duration = input.duration;
    params.easing = "ease-out";
    params.fromScale = input.scale ?? 0.8;
  } else if (!input.params && id === "cutvoke.anim.slideIn") {
    params.duration = input.duration;
    params.easing = "ease-out";
  } else if (!input.params && id === "cutvoke.anim.fadeOut") {
    params.duration = input.duration;
    params.easing = "linear";
  } else if (!input.params && id === "cutvoke.anim.zoomOut") {
    params.duration = input.duration;
    params.easing = "ease-out";
    params.toScale = input.scale ?? 1.0;
  } else if (!input.params && id === "cutvoke.anim.breathe") {
    params.amplitude = input.amplitude ?? 0.05;
    params.period = input.period ?? 2;
  }
  if (id && existing.some((effect) => effect.effectId === id
    && JSON.stringify(effect.params || {}) === JSON.stringify(params))) return { ok: true };
  const result = await runCommand(dispatch, state, "effect.setAnimation", {
    clipId: input.clipId,
    effectId: id,
    params,
    ...(input.slot ? { slot: input.slot } : {}),
    ...(input.keyframePolicy ? { keyframePolicy: input.keyframePolicy } : {}),
  });
  if (result.ok) dispatch({ type: "STATUS_SET", severity: "ok", text: id ? "已应用动画" : "已移除动画" });
  return result;
}

function findClip(state: EditorState, clipId: string) {
  const tracks = state.project?.sequence.tracks || [];
  for (const t of tracks) {
    const c = t.clips.find((x) => x.id === clipId);
    if (c) return c;
  }
  return undefined;
}
