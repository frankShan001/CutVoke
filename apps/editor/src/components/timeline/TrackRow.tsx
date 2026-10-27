import { msg, tr, useLocale } from "../../i18n";
/** 轨道泳道（配合共享拖拽系统）。
    - 片段整体拖动 → Timeline 全局拖拽（同轨 / 跨轨 = clip.move）。
    - 左/右缘拖拽 = clip.trim（本地 overlay + 释放提交，只改一端）。
    空白点击 = 取消选中；右键 = 上下文菜单。 */

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRightLeft, Film, Music, Type, Lock, Unlock, Volume2, VolumeX, Eye, EyeOff, Trash2, Link2 } from "lucide-react";
import type { Clip, Track } from "../../types/api";
import { useEditor } from "../../store/editor";
import { selectedClipIds, selectTimelineClip } from "../../store/timelineSelection";
import { getLatestState, runCommand } from "../../store/actions";
import { applyTransition, closeGapBeforeClip, trimClip, insertClipAutoTrack, insertClipFromDrop, moveClip, removeEffect, resolveInsertStart, splitClip, updateTrack } from "../../store/clipEdit";
import { ClipContextMenu, type CtxTarget } from "../ClipContextMenu";
import {
  clipStartSecs,
  clipEndSecs,
  frameDurationSecs,
  fmtTimePrecise,
  pxToSecSnapped,
  secsToFrameRatString,
  snapSecsToFrame,
  toPx,
} from "./util";
import { rationalToSecs } from "../../lib/rational";
import { mediaFitsTrack, uploadFiles } from "../../lib/importMedia";
import { assetDisplayName, getSessionAssets, inferKind, useSessionAssets, type SessionAsset } from "../../lib/assetStore";
import { probeMedia } from "../../lib/mediaApi";
import { effectLabel, type EffectSpec } from "../../lib/effects";
import { isTransitionEffectId, transitionDurationSecs, TRANSITION_DND_TYPE } from "../../lib/transitions";
import { EFFECT_DND_TYPE, getEffectDrag, setEffectDrag, clipAcceptsEffect, type EffectDragResource } from "../../lib/effectDrag";
import { applyResourceToClip } from "../../store/resourceEdit";
import type { TimelineDragApi } from "./useTimelineDrag";
import type { TimelineViewport } from "./useTimelineViewport";
import { ClipMediaStrip } from "./ClipMediaStrip";

interface TrimState {
  clipId: string;
  mode: "trim-l" | "trim-r";
  originStart: number;
  originEnd: number;
  startClientX: number;
  sourceStart: number;
  speedAbs: number;
  sourceDuration: number | null;
  curved: boolean;
}

interface TrimShape {
  left: number;
  width: number;
  edgePx: number;
  edgeSecs: number;
  durationSecs: number;
  mode: "trim-l" | "trim-r";
}

const sourceDurationCache = new Map<string, number | null>();
const sourceDurationPending = new Map<string, Promise<number | null>>();

function knownSourceDuration(path: string): number | null {
  const asset = getSessionAssets().find((item) => item.path === path);
  if (asset?.kind === "image") return null;
  return asset?.duration && asset.duration > 0 ? asset.duration : sourceDurationCache.get(path) ?? null;
}

async function sourceDurationFor(path: string): Promise<number | null> {
  const known = knownSourceDuration(path);
  if (known != null || inferKind(path, false, false) === "image") return known;
  const pending = sourceDurationPending.get(path);
  if (pending) return pending;
  const request = probeMedia(path).then((result) => {
    const duration = result.kind === "ok" && result.data.duration > 0 ? result.data.duration : null;
    if (duration != null) sourceDurationCache.set(path, duration);
    return duration;
  }).finally(() => sourceDurationPending.delete(path));
  sourceDurationPending.set(path, request);
  return request;
}

export const TrackRow = memo(function TrackRow({
  track,
  displayName,
  pxPerSec,
  dragApi,
  effectSpecs,
  viewport,
  visibleClipIds,
}: {
  track: Track;
  /** 人类可读轨道名（如「视频轨 1」），替代内部 track.id。 */
  displayName: string;
  pxPerSec: number;
  dragApi: TimelineDragApi;
  effectSpecs: EffectSpec[];
  viewport: TimelineViewport;
  visibleClipIds?: Set<string>;
}) {
  const locale = useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  useSessionAssets();
  const isAudio = track.kind === "audio";
  const isText = track.kind === "text";
  const frameRate = state.project?.sequence.fps;
  const frameDuration = frameDurationSecs(frameRate);
  // 轨道属性（后端 model 已有 locked/muted/visible；visible 缺省视为显示）
  const locked = !!track.locked;
  const agentLocked = !!state.editLock;
  const muted = !!track.muted;
  const hidden = track.visible === false;
  const [trim, setTrim] = useState<TrimState | null>(null);
  const [trimShape, setTrimShape] = useState<{ clipId: string; shape: TrimShape } | null>(null);
  const [menuTarget, setMenuTarget] = useState<CtxTarget | null>(null);
  const [dropOver, setDropOver] = useState(false);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const [sourceDurations, setSourceDurations] = useState<Record<string, number | null>>({});
  const [transitionDropAt, setTransitionDropAt] = useState<string | null>(null);
  const [effectDropAt, setEffectDropAt] = useState<{ clipId: string; allowed: boolean } | null>(null);
  const [transitionResize, setTransitionResize] = useState<{ clipId: string; duration: number } | null>(null);
  const transitionResizeRef = useRef<{
    pointerId: number;
    clipId: string;
    effectId: string;
    startX: number;
    startDuration: number;
    maxDuration: number;
    duration: number;
  } | null>(null);
  const trimPointerId = useRef<number | null>(null);
  const trimGesture = useRef(0);

  const laneFocused = state.selection?.trackId === track.id && !state.selection?.clipId;
  const isPrimaryVideo = !isAudio && state.project?.sequence.tracks.find((item) => item.kind === "video" && item.role !== "sticker")?.id === track.id;
  const orderedClips = useMemo(() => [...track.clips].sort((a, b) => clipStartSecs(a) - clipStartSecs(b)), [track.clips, locale]);
  const visibleClips = useMemo(() => track.clips.filter((clip) => (!visibleClipIds || visibleClipIds.has(clip.id)) && (
    track.clips.length <= 100 || (clipEndSecs(clip) >= viewport.start && clipStartSecs(clip) <= viewport.end) ||
    clip.id === state.selection?.clipId || clip.id === dragApi.active?.clipId || clip.id === trim?.clipId || clip.id === menuTarget?.clipId)),
  [track.clips, viewport, state.selection?.clipId, dragApi.active?.clipId, trim?.clipId, menuTarget?.clipId, locale,visibleClipIds]);
  const attachments = useMemo(() => {
    const counts = new Map<string, number>();
    for (const other of state.project?.sequence.tracks || []) for (const clip of other.clips) {
      if (clip.attachedToClipId) counts.set(clip.attachedToClipId, (counts.get(clip.attachedToClipId) || 0) + 1);
    }
    return counts;
  }, [state.project, locale]);
  const previousClips = useMemo(() => new Map(orderedClips.map((clip, index) => [clip.id, orderedClips[index - 1]])), [orderedClips, locale]);
  const visibleGaps = useMemo(() => isPrimaryVideo && !visibleClipIds ? orderedClips.flatMap((clip, index) => {
    const start = index > 0 ? clipEndSecs(orderedClips[index - 1]) : 0;
    const end = clipStartSecs(clip);
    if (end - start < frameDuration / 2 || (track.clips.length > 100 && (end < viewport.start || start > viewport.end))) return [];
    const covered = state.project?.sequence.tracks.some((other) =>
      other.id !== track.id && other.kind === "video" && other.visible !== false &&
      other.clips.some((item) => clipStartSecs(item) < end && clipEndSecs(item) > start));
    return [{ clipId: clip.id, start, end, label: covered ? tr("主轨空隙") : tr("黑场空隙") }];
  }) : [], [isPrimaryVideo, orderedClips, frameDuration, viewport, state.project, track.id, locale,visibleClipIds]);

  // Source metadata is only needed for a selected clip's trim handles. Library
  // durations already exist; probing every clip here starved preview requests.
  const sourcePathsKey = state.selection?.trackId === track.id
    ? track.clips.find((clip) => clip.id === state.selection?.clipId)?.assetRef.sourcePath || "" : "";
  useEffect(() => {
    let cancelled = false;
    const paths = [...new Set(sourcePathsKey.split("\u0000").filter(Boolean))];
    void Promise.all(paths.map(async (path) => [path, await sourceDurationFor(path)] as const)).then((entries) => {
      if (!cancelled) setSourceDurations(Object.fromEntries(entries));
    });
    return () => {
      cancelled = true;
    };
  }, [sourcePathsKey]);

  // 切换轨道属性：走 track.update 命令（缺省字段不变）
  const handleToggle = (field: "locked" | "muted" | "visible", value: boolean) => {
    const st = getLatestState() || state;
    void updateTrack(dispatch, st, { trackId: track.id, [field]: value });
  };

  // 切割模式：点击片段 → 在播放头处分割（若播放头在片段内）；否则选择
  const handleCutClick = (e: React.MouseEvent | React.KeyboardEvent, clip: Clip) => {
    if (dragApi.consumeClick()) return;
    if (state.toolMode === "cut") {
      e.preventDefault();
      e.stopPropagation();
      if (locked) {
        dispatch({ type: "STATUS_SET", severity: "warn", text: msg("此轨道已锁定，解锁后才能切割片段") });
        return;
      }
      const cs = rationalToSecs(clip.timelineStart);
      const ce = rationalToSecs(clip.timelineEnd);
      const st = getLatestState() || state;
      if (st.playhead > cs + frameDuration / 2 && st.playhead < ce - frameDuration / 2) {
        void splitClip(dispatch, st, { clipId: clip.id, time: secsToFrameRatString(st.playhead, frameRate) });
      } else {
        dispatch({ type: "STATUS_SET", severity: "warn", text: msg("播放头需在片段内部才能分割") });
      }
    } else {
      selectTimelineClip(dispatch, getLatestState() || state, clip.id, e);
      if (e.ctrlKey || e.metaKey || e.shiftKey) return;
      const start = rationalToSecs(clip.timelineStart);
      const end = rationalToSecs(clip.timelineEnd);
      const playhead = (getLatestState() || state).playhead;
      if (playhead < start || playhead >= end) {
        const offset = "clientX" in e && e.detail !== 0
          ? Math.max(0, e.clientX - e.currentTarget.getBoundingClientRect().left) / pxPerSec : 0;
        const time = Math.min(Math.max(start, end - frameDuration), start + offset);
        dispatch({ type: "PLAYHEAD_SET", t: Math.max(start, Math.floor((time + 1e-7) / frameDuration) * frameDuration) });
      }
    }
  };

  // ---- 拖入本轨（HTML5 DnD）：库内素材 or OS 文件 ----
  const handleDragOver = (e: React.DragEvent) => {
    const types = e.dataTransfer.types;
    if (!types.includes("text/cutvoke-media") && !types.includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = locked ? "none" : "copy";
    if (!locked) {
      if (!dropOver) setDropOver(true);
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      const raw = Math.max(0, (e.clientX - rect.left) / pxPerSec);
      const target = rationalToSecs(resolveInsertStart(getLatestState() || state, track.id, raw));
      setDropAt((previous) => previous != null && Math.abs(previous - target) < 1e-4 ? previous : target);
    }
  };

  const onTransitionDragOver = (event: React.DragEvent<HTMLElement>, clipId: string) => {
    if (!event.dataTransfer.types.includes(TRANSITION_DND_TYPE)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = locked || agentLocked ? "none" : "copy";
    if (!locked && !agentLocked) setTransitionDropAt(clipId);
  };

  const onEffectDragOver = (event: React.DragEvent<HTMLElement>, clip: Clip) => {
    const transition = event.dataTransfer.types.includes(TRANSITION_DND_TYPE);
    if (!transition && !event.dataTransfer.types.includes(EFFECT_DND_TYPE)) return;
    event.preventDefault(); event.stopPropagation();
    const resource = getEffectDrag();
    const allowed = !transition && !locked && !agentLocked && (!resource || clipAcceptsEffect(resource, track, clip));
    event.dataTransfer.dropEffect = allowed ? "copy" : "none";
    setEffectDropAt(previous => previous?.clipId === clip.id && previous.allowed === allowed ? previous : { clipId: clip.id, allowed });
  };
  const onEffectDrop = (event: React.DragEvent<HTMLElement>, clip: Clip) => {
    const transition = event.dataTransfer.types.includes(TRANSITION_DND_TYPE);
    if (!transition && !event.dataTransfer.types.includes(EFFECT_DND_TYPE)) return;
    event.preventDefault(); event.stopPropagation(); setEffectDropAt(null); setEffectDrag(null);
    if (transition) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: msg("转场请拖到两个片段之间的接缝") }); return;
    }
    try {
      const payload = JSON.parse(event.dataTransfer.getData(EFFECT_DND_TYPE)) as EffectDragResource;
      const effect = effectSpecs.find(e => e.effectId === payload.effectId);
      if (!effect || effect.category === "transition" || !Array.isArray(payload.appliesTo)
        || (payload.presetId !== undefined && typeof payload.presetId !== "string")) throw new Error(tr("无效效果资源"));
      void applyResourceToClip(dispatch, getLatestState() || state, payload, effect, clip.id);
    } catch (error) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: error instanceof Error ? error.message : msg("无效效果资源") });
    }
  };

  const onTransitionDrop = (event: React.DragEvent<HTMLElement>, clip: Clip, previous: Clip | undefined) => {
    if (!event.dataTransfer.types.includes(TRANSITION_DND_TYPE)) return;
    event.preventDefault();
    event.stopPropagation();
    setTransitionDropAt(null);
    setEffectDrag(null);
    if (locked || agentLocked) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: msg("轨道已锁定或 Agent 正在编辑，不能应用转场") });
      return;
    }
    if (!previous || Math.abs(clipStartSecs(clip) - clipEndSecs(previous)) > 0.001) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: msg("两段之间有空隙，先贴合片段再应用转场") });
      return;
    }
    try {
      const payload = JSON.parse(event.dataTransfer.getData(TRANSITION_DND_TYPE)) as { effectId?: string; duration?: number; presetId?: string };
      if (!payload.effectId || !isTransitionEffectId(payload.effectId)) throw new Error(tr("无效转场资源"));
      const maxDuration = Math.min(5,
        (clipEndSecs(previous) - clipStartSecs(previous)) / 2,
        (clipEndSecs(clip) - clipStartSecs(clip)) / 2);
      if (maxDuration < 0.1) throw new Error(tr("相邻片段过短，无法形成转场"));
      const requested = typeof payload.duration === "number" && Number.isFinite(payload.duration)
        ? payload.duration : 1;
      const duration = Math.round(Math.min(maxDuration, Math.max(0.1, requested)) * 1000) / 1000;
      const latest = getLatestState() || state;
      const operation = typeof payload.presetId === "string"
        ? runCommand(dispatch, latest, "builtinPreset.apply", { clipId: clip.id, presetId: payload.presetId, duration })
        : applyTransition(dispatch, latest, { clipId: clip.id, effectId: payload.effectId, duration });
      void operation
        .then((result) => {
          if (!result.ok) return;
          dispatch({ type: "SELECTION_SET", selection: { trackId: track.id, clipId: clip.id,
            effectId: payload.effectId, effectKind: "transition" } });
          dispatch({ type: "PLAYHEAD_SET", t: clipStartSecs(clip) });
          window.dispatchEvent(new Event("cutvoke:open-transition"));
          window.dispatchEvent(new CustomEvent("cutvoke:edit-effect", { detail: { effectId: payload.effectId, category: "transition" } }));
        });
    } catch (error) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: error instanceof Error ? error.message : msg("无效转场资源") });
    }
  };

  const startTransitionResize = (event: React.PointerEvent<HTMLButtonElement>,
    clip: Clip, effectId: string, duration: number, maxDuration: number) => {
    if (event.button !== 0 || locked || agentLocked || maxDuration < 0.1) return;
    event.preventDefault();
    event.stopPropagation();
    transitionResizeRef.current = {
      pointerId: event.pointerId, clipId: clip.id, effectId,
      startX: event.clientX, startDuration: duration, maxDuration, duration,
    };
    setTransitionResize({ clipId: clip.id, duration });
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const moveTransitionResize = (event: React.PointerEvent<HTMLButtonElement>) => {
    const active = transitionResizeRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    const fps = state.project?.sequence.fps;
    const frame = frameDurationSecs(fps);
    // The marker is centred on the cut; its right handle moves by half of a
    // duration change. Snap the submitted duration to the project frame grid.
    const raw = active.startDuration + 2 * (event.clientX - active.startX) / pxPerSec;
    const bounded = Math.max(0.1, Math.min(active.maxDuration, raw));
    const snapped = Math.max(0.1, Math.min(active.maxDuration, Math.round(bounded / frame) * frame));
    active.duration = Math.round(snapped * 1000) / 1000;
    setTransitionResize({ clipId: active.clipId, duration: active.duration });
  };

  const finishTransitionResize = (event: React.PointerEvent<HTMLButtonElement>, cancel = false) => {
    const active = transitionResizeRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    transitionResizeRef.current = null;
    setTransitionResize(null);
    if (cancel || locked || agentLocked || Math.abs(active.duration - active.startDuration) < 0.001) return;
    const latest = getLatestState() || state;
    void applyTransition(dispatch, latest, {
      clipId: active.clipId, effectId: active.effectId, duration: active.duration,
    });
  };

  const nudgeClip = (clip: Clip, direction: -1 | 1) => {
    if (locked || agentLocked) return;
    const latest = getLatestState() || state;
    const currentTrack = latest.project?.sequence.tracks.find((item) => item.id === track.id);
    const ordered = [...(currentTrack?.clips || [])].sort((a, b) => clipStartSecs(a) - clipStartSecs(b));
    const index = ordered.findIndex((item) => item.id === clip.id);
    const neighbor = ordered[index + direction];
    if (!neighbor) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: msg("已经是该轨道的首段或末段") });
      return;
    }
    void moveClip(dispatch, latest, {
      clipId: clip.id,
      mode: "reorder",
      anchorClipId: neighbor.id,
      anchorPosition: direction < 0 ? "before" : "after",
    });
  };
  const handleDragLeave = (e: React.DragEvent) => {
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDropOver(false);
    setDropAt(null);
  };
  const handleDrop = (e: React.DragEvent) => {
    setDropOver(false);
    setDropAt(null);
    const files = e.dataTransfer.files;
    const hasFiles = !!files && files.length > 0;
    const raw = hasFiles ? "" : e.dataTransfer.getData("text/cutvoke-media");
    if (!hasFiles && !raw) return;
    e.preventDefault();
    e.stopPropagation();
    if (locked) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: msg("此轨道已锁定，解锁后才能添加素材") });
      return;
    }
    // 计算 drop 时间线位置（px→sec）
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    // 轨道随 scroller 一起移动，rect.left 已反映横向滚动偏移。
    const startSecs = Math.max(0, (e.clientX - rect.left) / pxPerSec);
    const ctx = getLatestState() || state;
    if (hasFiles) {
      // OS 文件拖入：上传 → 依次插入本轨（按累计时长错位，避免重叠）
      let cursor = startSecs;
      void uploadFiles(
        Array.from(files),
        (media) => {
          if (!mediaFitsTrack(media.kind, track.kind)) {
            dispatch({
              type: "STATUS_SET",
              severity: "warn",
              text: media.kind === "audio"
                ? msg("音频素材不能放到视频轨；拖到下方空白处会自动创建音频轨")
                : msg("此素材不是音频，不能放到音频轨"),
            });
            return;
          }
          const at = cursor;
          cursor += media.duration && media.duration > 0 ? media.duration : 2;
          return insertClipFromDrop(dispatch, getLatestState() || ctx, {
            trackId: track.id,
            sourcePath: media.path,
            assetId: media.assetId,
            timelineStartSecs: at,
          });
        },
        (name, message) => dispatch({ type: "STATUS_SET", severity: "warn", text: msg("导入 {0} 失败：{1}", name, message) }),
      );
      return;
    }
    let payload: { sourcePath: string; assetId?: string; kind?: SessionAsset["kind"]; role?: "sticker";
      stickerAnimation?: { effectId: string; params: Record<string, unknown> };
      stickerScale?: number;
      resourceRef?: import("../../types/api").ResourceReference };
    try {
      payload = JSON.parse(raw);
    } catch {
      return;
    }
    if (payload.role === "sticker") {
      void insertClipAutoTrack(dispatch, ctx, {
        sourcePath: payload.sourcePath, assetId: payload.assetId,
        trackKind: "video", trackRole: "sticker", timelineStartSecs: startSecs,
        stickerAnimation: payload.stickerAnimation,
        resourceRef: payload.resourceRef,
        stickerScale: payload.stickerScale,
      });
      return;
    }
    const mediaKind = payload.kind ?? inferKind(payload.sourcePath, false, false);
    if (!mediaFitsTrack(mediaKind, track.kind)) {
      dispatch({
        type: "STATUS_SET",
        severity: "warn",
        text: mediaKind === "audio"
          ? msg("音频素材不能放到视频轨；拖到下方空白处会自动创建音频轨")
          : msg("此素材不是音频，不能放到音频轨"),
      });
      return;
    }
    void insertClipFromDrop(dispatch, ctx, {
      trackId: track.id,
      sourcePath: payload.sourcePath,
      assetId: payload.assetId,
      timelineStartSecs: startSecs,
    });
  };

  const beginTrim = (e: React.PointerEvent, clip: Clip, mode: "trim-l" | "trim-r") => {
    if (agentLocked) return;
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    trimGesture.current += 1;
    const gesture = trimGesture.current;
    setTrim({
      clipId: clip.id,
      mode,
      originStart: clipStartSecs(clip),
      originEnd: clipEndSecs(clip),
      startClientX: e.clientX,
      sourceStart: rationalToSecs(clip.sourceStart),
      speedAbs: Math.max(0.000001, Math.abs(rationalToSecs(clip.speed || { num: "1", den: "1" }))),
      sourceDuration: knownSourceDuration(clip.assetRef.sourcePath) ?? sourceDurations[clip.assetRef.sourcePath] ?? null,
      curved: Boolean(clip.speedCurve),
    });
    trimPointerId.current = e.pointerId;
    void sourceDurationFor(clip.assetRef.sourcePath).then((duration) => {
      if (trimGesture.current === gesture && trimPointerId.current != null) {
        setTrim((current) => current?.clipId === clip.id ? { ...current, sourceDuration: duration } : current);
      }
    });
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  const boundedTrimStart = (value: number, current: TrimState, minDuration: number) => {
    if (current.curved) return Math.min(current.originEnd - minDuration,
      Math.max(current.originStart, value));
    let minimum = 0;
    if (current.sourceDuration != null) {
      const maxTimelineDuration = Math.max(minDuration, (current.sourceDuration - current.sourceStart) / current.speedAbs);
      minimum = Math.max(0, current.originEnd - maxTimelineDuration);
    }
    return Math.min(current.originEnd - minDuration, Math.max(minimum, value));
  };

  const boundedTrimEnd = (value: number, current: TrimState, minDuration: number) => {
    if (current.curved) return Math.min(current.originEnd,
      Math.max(current.originStart + minDuration, value));
    let maximum = Number.POSITIVE_INFINITY;
    if (current.sourceDuration != null) {
      const maxTimelineDuration = Math.max(minDuration, (current.sourceDuration - current.sourceStart) / current.speedAbs);
      maximum = current.originStart + maxTimelineDuration;
    }
    return Math.min(maximum, Math.max(current.originStart + minDuration, value));
  };

  const handleTrimMove = (e: React.PointerEvent) => {
    if (trimPointerId.current !== e.pointerId || !trim) return;
    const deltaPx = e.clientX - trim.startClientX;
    if (Math.abs(deltaPx) < 4) return;
    const snap = pxToSecSnapped(deltaPx, pxPerSec, frameRate);
    if (trim.mode === "trim-l") {
      const ns = boundedTrimStart(snapSecsToFrame(trim.originStart + snap, frameRate), trim, frameDuration);
      setTrimShape({ clipId: trim.clipId, shape: {
        left: toPx(ns, pxPerSec), width: toPx(trim.originEnd - ns, pxPerSec),
        edgePx: toPx(ns, pxPerSec), edgeSecs: ns,
        durationSecs: trim.originEnd - ns, mode: trim.mode,
      } });
    } else {
      const ne = boundedTrimEnd(snapSecsToFrame(trim.originEnd + snap, frameRate), trim, frameDuration);
      setTrimShape({ clipId: trim.clipId, shape: {
        left: toPx(trim.originStart, pxPerSec), width: toPx(ne - trim.originStart, pxPerSec),
        edgePx: toPx(ne, pxPerSec), edgeSecs: ne,
        durationSecs: ne - trim.originStart, mode: trim.mode,
      } });
    }
  };

  const handleTrimUp = async (e: React.PointerEvent) => {
    if (trimPointerId.current !== e.pointerId || !trim) return;
    trimPointerId.current = null;
    const t = { ...trim };
    const gesture = trimGesture.current;
    if (agentLocked) {
      setTrim(null);
      setTrimShape(null);
      return;
    }
    const deltaPx = e.clientX - trim.startClientX;
    if (Math.abs(deltaPx) < 4) {
      setTrim(null);
      setTrimShape(null);
      return;
    }
    const snap = pxToSecSnapped(deltaPx, pxPerSec, frameRate);
    const source = track.clips.find((clip) => clip.id === t.clipId)?.assetRef.sourcePath;
    if (t.sourceDuration == null && source) t.sourceDuration = await sourceDurationFor(source);
    if (trimGesture.current !== gesture) return;
    const st = getLatestState();
    const ctx = st && st.currentId ? st : state;
    const currentClip = ctx.project?.sequence.tracks.flatMap((item) => item.clips).find((item) => item.id === t.clipId);
    if (ctx.currentId !== state.currentId || ctx.editLock || !currentClip ||
        clipStartSecs(currentClip) !== t.originStart || clipEndSecs(currentClip) !== t.originEnd) {
      setTrim(null);
      setTrimShape(null);
      return;
    }
    const commitFrameRate = ctx.project?.sequence.fps ?? frameRate;
    const minDuration = frameDurationSecs(commitFrameRate);
    if (t.mode === "trim-l") {
      const requested = snapSecsToFrame(t.originStart + snap, commitFrameRate);
      const ns = boundedTrimStart(requested, t, minDuration);
      if (Math.abs(ns - t.originStart) >= minDuration / 2) {
        void trimClip(dispatch, ctx, { clipId: t.clipId, newStart: secsToFrameRatString(ns, commitFrameRate) });
      }
    } else {
      const requested = snapSecsToFrame(t.originEnd + snap, commitFrameRate);
      const ne = boundedTrimEnd(requested, t, minDuration);
      if (Math.abs(ne - t.originEnd) >= minDuration / 2) {
        void trimClip(dispatch, ctx, { clipId: t.clipId, newEnd: secsToFrameRatString(ne, commitFrameRate) });
      }
      if (t.sourceDuration != null && ne < requested - minDuration / 2) {
        dispatch({ type: "STATUS_SET", severity: "warn", text: msg("已到源素材末尾，不能继续延长") });
      }
    }
    setTrim(null);
    setTrimShape(null);
  };

  const cancelTrim = useCallback(() => {
    trimGesture.current += 1;
    trimPointerId.current = null;
    setTrim(null);
    setTrimShape(null);
  }, []);

  useEffect(() => {
    if (agentLocked) {
      dragApi.cancel();
      cancelTrim();
    }
  }, [agentLocked, dragApi.cancel, cancelTrim]);

  return (
    <div
      className={`timeline-track${locked ? " timeline-track--locked" : ""}${muted ? " timeline-track--muted" : ""}${hidden ? " timeline-track--hidden" : ""}`}
    >
      <div className="timeline-track__label" title={displayName}>
        <span className={`timeline-track__kind timeline-track__kind--${isAudio ? "audio" : isText ? "text" : "video"}`}>
          {isAudio ? <Music size={11} /> : isText ? <Type size={11} /> : <Film size={11} />}
        </span>
        <span className="timeline-track__name">{displayName}</span>
        <div className="track-toggles">
          <button
            className={`track-toggle${locked ? " track-toggle--on" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              handleToggle("locked", !locked);
            }}
            aria-label={locked ? tr("解锁轨道 {0}", track.id) : tr("锁定轨道 {0}", track.id)}
            aria-pressed={locked}
            title={locked ? tr("解锁轨道") : tr("锁定轨道")}
          >
            {locked ? <Lock size={13} /> : <Unlock size={13} />}
          </button>
          <button
            className={`track-toggle${muted ? " track-toggle--on" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              handleToggle("muted", !muted);
            }}
            aria-label={muted ? tr("取消静音 {0}", track.id) : tr("静音轨道 {0}", track.id)}
            aria-pressed={muted}
            title={muted ? tr("取消静音") : tr("静音轨道")}
          >
            {muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
          </button>
          <button
            className={`track-toggle${hidden ? " track-toggle--on" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              handleToggle("visible", !hidden);
            }}
            aria-label={hidden ? tr("显示轨道 {0}", track.id) : tr("隐藏轨道 {0}", track.id)}
            aria-pressed={hidden}
            title={hidden ? tr("显示轨道") : tr("隐藏轨道")}
          >
            {hidden ? <EyeOff size={13} /> : <Eye size={13} />}
          </button>
        </div>
      </div>
      <div
        className={`timeline-track__lane ${laneFocused ? "timeline-track__lane--focused" : ""} ${dropOver ? "timeline-track__lane--drop" : ""}`}
        data-track-id={track.id}
        data-track-kind={track.kind}
        data-track-locked={locked ? "true" : "false"}
        title={locked ? tr("此轨道已锁定；解锁后可编辑或添加素材") : undefined}
        onClick={() => {
          if (state.selection?.trackId === track.id) dispatch({ type: "SELECTION_SET", selection: null });
          else dispatch({ type: "SELECTION_SET", selection: { trackId: track.id } });
        }}
        onPointerMove={(e) => {
          dragApi.move(e);
          handleTrimMove(e);
        }}
        onPointerUp={(e) => {
          dragApi.finish(e);
          handleTrimUp(e);
        }}
        onPointerCancel={() => {
          dragApi.cancel();
          cancelTrim();
        }}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {dropOver && dropAt !== null ? (
          <div
            className="timeline-track__insert-target"
            style={{ left: toPx(dropAt, pxPerSec) }}
            role="status"
            aria-label={tr("在 {0} 秒的片段边界插入素材", dropAt.toFixed(2))}
          >
            <span>{tr("插入")}</span>
          </div>
        ) : null}
        {track.clips.length === 0 ? <span className="timeline-track__lane-empty">{tr("空轨道")}</span> : null}
        {visibleGaps.map((gap) => (
          <button
            key={`gap-${gap.clipId}`}
            type="button"
            className="timeline-track__gap"
            style={{ left: toPx(gap.start, pxPerSec), width: Math.max(6, toPx(gap.end - gap.start, pxPerSec)) }}
            disabled={locked || agentLocked}
            aria-label={tr("{0} {1} 到 {2} 秒，时长 {3} 秒；关闭此空隙", gap.label, gap.start.toFixed(2), gap.end.toFixed(2), (gap.end - gap.start).toFixed(2))}
            title={tr("{0} {1}–{2}s · 点击关闭此处空隙，后续片段左移", gap.label, gap.start.toFixed(2), gap.end.toFixed(2))}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              const latest = getLatestState() || state;
              void closeGapBeforeClip(dispatch, latest, { trackId: track.id, beforeClipId: gap.clipId });
            }}
          >
            <span>{gap.label} {(gap.end - gap.start).toFixed(2)}s</span>
            <span className="timeline-track__gap-close">{tr("合拢")}</span>
          </button>
        ))}
        {visibleClips.map((clip) => (
          <ClipBlock
            key={clip.id}
            clip={clip}
            isAudio={isAudio}
            isText={isText}
            attachedCount={attachments.get(clip.id) || 0}
            pxPerSec={pxPerSec}
            viewport={viewport}
            locked={locked}
            selected={selectedClipIds(state).includes(clip.id)}
            trimOverlay={trimShape?.clipId === clip.id ? trimShape.shape : undefined}
            effectDropState={effectDropAt?.clipId === clip.id ? (effectDropAt.allowed ? "allowed" : "denied") : undefined}
            onEffectDragOver={event => onEffectDragOver(event, clip)}
            onEffectDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setEffectDropAt(null); }}
            onEffectDrop={event => onEffectDrop(event, clip)}
            onDragStart={(e, mode) => {
              if (mode === "move") {
                if (state.toolMode !== "cut") dragApi.begin(e, clip, track, "move", pxPerSec);
              }
              else beginTrim(e, clip, mode);
            }}
            onContextMenu={(e) => {
              if (locked) {
                e.preventDefault();
                e.stopPropagation();
                dispatch({ type: "STATUS_SET", severity: "warn", text: msg("此轨道已锁定，解锁后才能编辑片段") });
                return;
              }
              e.preventDefault();
              e.stopPropagation();
              if (!selectedClipIds(state).includes(clip.id)) selectTimelineClip(dispatch, state, clip.id);
              setMenuTarget({ x: e.clientX, y: e.clientY, clipId: clip.id, clip, trackId: track.id, playheadSecs: (getLatestState() || state).playhead });
            }}
            onCutClick={(e) => handleCutClick(e, clip)}
            onNudge={(direction) => nudgeClip(clip, direction)}
          />
        ))}
        {track.kind === "video"
          ? visibleClips.map((clip) => {
                const previous = previousClips.get(clip.id);
                if (!previous) return null;
                const cutAt = clipStartSecs(clip);
                if (Math.abs(cutAt - clipEndSecs(previous)) > 0.001) return null;
                if ((clip.effects || []).some((effect) => isTransitionEffectId(String(effect.effectId)))) return null;
                return (
                  <button
                    key={`seam-${previous.id}-${clip.id}`}
                    type="button"
                    className={`transition-seam${transitionDropAt === clip.id ? " transition-seam--drop" : ""}`}
                    style={{ left: toPx(cutAt, pxPerSec) }}
                    aria-label={tr("在 {0} 秒的片段接缝添加转场", cutAt.toFixed(2))}
                    title={tr("点击打开转场面板，或从资源库拖入转场")}
                    onPointerDown={(event) => event.stopPropagation()}
                    onDragOver={(event) => onTransitionDragOver(event, clip.id)}
                    onDragLeave={() => setTransitionDropAt(null)}
                    onDrop={(event) => onTransitionDrop(event, clip, previous)}
                    onClick={(event) => {
                      event.stopPropagation();
                      dispatch({ type: "SELECTION_SET", selection: { trackId: track.id, clipId: clip.id } });
                      dispatch({ type: "PLAYHEAD_SET", t: cutAt });
                      window.dispatchEvent(new Event("cutvoke:open-transition"));
                    }}
                  >
                    <ArrowRightLeft size={11} aria-hidden="true" />
                  </button>
                );
              })
          : null}
        {track.kind === "video"
          ? visibleClips.flatMap((clip) =>
              (clip.effects || [])
                .filter((effect) => isTransitionEffectId(String(effect.effectId)))
                .map((effect, index) => {
                  const effectId = String(effect.effectId);
                  const label = effectLabel(effectSpecs, effectId);
                  const duration = transitionDurationSecs(effect);
                  const cutAt = clipStartSecs(clip);
                  const previous = previousClips.get(clip.id);
                  const connected = !!previous && Math.abs(clipEndSecs(previous) - cutAt) <= 0.001;
                  const maxDuration = connected
                    ? Math.min(5, (clipEndSecs(previous) - clipStartSecs(previous)) / 2,
                        (clipEndSecs(clip) - cutAt) / 2)
                    : 0;
                  const actualDuration = Math.min(duration, maxDuration);
                  const liveDuration = transitionResize?.clipId === clip.id
                    ? transitionResize.duration : actualDuration;
                  const width = Math.max(56, toPx(liveDuration, pxPerSec));
                  const selected =
                    state.selection?.trackId === track.id &&
                    state.selection?.clipId === clip.id &&
                    state.selection?.effectId === effectId &&
                    state.selection?.effectKind === "transition";
                  return (
                    <div
                      key={`${clip.id}-${effectId}-${index}`}
                      className={`transition-marker${selected ? " transition-marker--selected" : ""}${connected ? "" : " transition-marker--inactive"}${transitionDropAt === clip.id ? " transition-marker--drop" : ""}`}
                      style={{ left: toPx(cutAt, pxPerSec) - width / 2, width }}
                      title={connected
                        ? tr("{0} · 实际 {1}s · 拖右侧手柄调整时长", label, liveDuration.toFixed(2))
                        : tr("{0} · 接缝已断开，暂不生效 · 单击查看或删除", label)}
                      onPointerDown={(event) => event.stopPropagation()}
                      onDragOver={(event) => onTransitionDragOver(event, clip.id)}
                      onDragLeave={() => setTransitionDropAt(null)}
                      onDrop={(event) => onTransitionDrop(event, clip, previous)}
                    >
                      <button
                        className="transition-marker__select"
                        type="button"
                        aria-pressed={selected}
                        aria-label={tr("{0} 转场，位于 {1} 秒{2}", label, cutAt.toFixed(2), connected ? "" : tr("，接缝已断开，暂不生效"))}
                        onClick={(event) => {
                          event.stopPropagation();
                          dispatch({
                            type: "SELECTION_SET",
                            selection: { trackId: track.id, clipId: clip.id, effectId, effectKind: "transition" },
                          });
                          dispatch({ type: "PLAYHEAD_SET", t: cutAt });
                          window.dispatchEvent(new Event("cutvoke:open-transition"));
                        }}
                      >
                        <ArrowRightLeft size={11} />
                        <span className="transition-marker__name">{transitionResize?.clipId === clip.id ? `${liveDuration.toFixed(2)}s` : label}</span>
                      </button>
                      <button
                        className="transition-marker__delete"
                        type="button"
                        disabled={locked || agentLocked}
                        aria-label={tr("删除转场 {0}", label)}
                        title={locked || agentLocked ? tr("轨道已锁定") : tr("删除转场")}
                        onClick={(event) => {
                          event.stopPropagation();
                          const latest = getLatestState() || state;
                          void removeEffect(dispatch, latest, { clipId: clip.id, effectId }).then((result) => {
                            if (result.ok) dispatch({ type: "SELECTION_SET", selection: null });
                          });
                        }}
                      >
                        <Trash2 size={9} />
                      </button>
                      <button
                        type="button"
                        className="transition-marker__resize"
                        disabled={locked || agentLocked || !connected || maxDuration < 0.1}
                        aria-label={tr("调整转场 {0} 时长，当前 {1} 秒", label, liveDuration.toFixed(2))}
                        title={tr("拖动调整转场时长，最长 {0} 秒", maxDuration.toFixed(2))}
                        onPointerDown={(event) => startTransitionResize(event, clip, effectId, actualDuration, maxDuration)}
                        onPointerMove={moveTransitionResize}
                        onPointerUp={(event) => finishTransitionResize(event)}
                        onPointerCancel={(event) => finishTransitionResize(event, true)}
                        onClick={(event) => event.stopPropagation()}
                      />
                    </div>
                  );
                }),
            )
          : null}
        {trimShape ? (() => {
          const { edgePx, edgeSecs, durationSecs, mode } = trimShape.shape;
          const edgeFrame = Math.round(edgeSecs / frameDuration);
          const durationFrames = Math.round(durationSecs / frameDuration);
          return (
            <div
              className="timeline-track__trim-readout"
              style={{ left: Math.max(2, edgePx - 116) }}
              role="status"
              aria-live="polite"
              data-testid="clip-trim-readout"
              data-edge-seconds={edgeSecs.toFixed(6)}
              data-edge-frame={edgeFrame}
              data-duration-frames={durationFrames}
            >
              {mode === "trim-l" ? tr("起点") : tr("终点")} {fmtTimePrecise(edgeSecs)} · {edgeFrame}{tr(" 帧 · 片段 ")}{durationFrames}{tr(" 帧 / ")}{durationSecs.toFixed(3)}s
            </div>
          );
        })() : null}
      </div>
      {menuTarget ? (
        <ClipContextMenu target={menuTarget} onClose={() => setMenuTarget(null)} dispatch={dispatch} />
      ) : null}
    </div>
  );
});

function ClipBlock({
  clip,
  isAudio,
  isText,
  attachedCount,
  pxPerSec,
  viewport,
  locked,
  selected,
  trimOverlay,
  onDragStart,
  onContextMenu,
  onCutClick,
  onNudge,
  effectDropState,
  onEffectDragOver,
  onEffectDragLeave,
  onEffectDrop,
}: {
  clip: Clip;
  isAudio: boolean;
  isText: boolean;
  attachedCount: number;
  pxPerSec: number;
  viewport: TimelineViewport;
  locked?: boolean;
  selected: boolean;
  trimOverlay?: TrimShape;
  onDragStart: (e: React.PointerEvent, mode: "move" | "trim-l" | "trim-r") => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onCutClick: (e: React.MouseEvent | React.KeyboardEvent) => void;
  onNudge: (direction: -1 | 1) => void;
  effectDropState?: "allowed" | "denied";
  onEffectDragOver: (e: React.DragEvent<HTMLElement>) => void;
  onEffectDragLeave: (e: React.DragEvent<HTMLElement>) => void;
  onEffectDrop: (e: React.DragEvent<HTMLElement>) => void;
}) {
  useLocale();
  const baseLeft = toPx(clipStartSecs(clip), pxPerSec);
  const durSecs = clipEndSecs(clip) - clipStartSecs(clip);
  const baseWidth = toPx(durSecs, pxPerSec);
  const left = trimOverlay?.left ?? baseLeft;
  const width = trimOverlay ? Math.max(trimOverlay.width, 8) : Math.max(baseWidth, 8);
  const titleContent = clip.effects?.find((effect) => effect.effectId === "cutvoke.text")?.params?.content;
  const name = isText ? String(titleContent || tr("未命名标题")) : clip.role==='adjustment'?
    clip.effects?.some(effect=>effect.effectId==='cutvoke.fx.scene3d')?tr('三维场景'):tr('调整层'):assetDisplayName(clip.assetRef);

  return (
    <div
      className={`clip-block clip-block--${isAudio ? "audio" : isText ? "text" : "video"} ${selected ? "clip-block--selected" : ""}${effectDropState ? ` clip-block--effect-${effectDropState}` : ""}`}
      data-clip-id={clip.id}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={tr("{0}，{1} 秒，开始于 {2} 秒{3}{4}{5}；Alt 加左右方向键可与相邻片段交换顺序", name, durSecs.toFixed(1), clipStartSecs(clip).toFixed(3), clip.attachedToClipId ? tr("，跟随视频移动") : "", attachedCount ? tr("，有 {0} 个关联片段", attachedCount) : "", selected ? tr("，已选中") : "")}
      style={{ left, width }}
      onDragOver={onEffectDragOver} onDragLeave={onEffectDragLeave} onDrop={onEffectDrop}
      onClick={(e) => {
        e.stopPropagation();
        onCutClick(e);
      }}
      onKeyDown={(e) => {
        if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
          e.preventDefault();
          e.stopPropagation();
          onNudge(e.key === "ArrowLeft" ? -1 : 1);
          return;
        }
        if (!e.repeat && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onCutClick(e);
        }
      }}
      onContextMenu={onContextMenu}
      onPointerDown={(e) => {
        if (!locked) onDragStart(e, "move");
      }}
      title={tr("{0} · {1}s{2}{3} · Alt+←/→ 调整顺序", name, durSecs.toFixed(1), clip.attachedToClipId ? tr(" · 跟随视频") : "", attachedCount ? tr(" · {0} 个片段跟随", attachedCount) : "")}
    >
      <ClipMediaStrip clip={clip} isAudio={isAudio} isText={isText} pxPerSec={pxPerSec} visibleStart={viewport.start} visibleEnd={viewport.end} />
      <div className="clip-block__name">{name}</div>
      {clip.attachedToClipId || attachedCount ? <span className="clip-block__meta" aria-hidden="true"><Link2 size={11} />{attachedCount || ""}</span> : null}
      <div className="clip-block__meta">{durSecs.toFixed(1)}s</div>
      {effectDropState ? <span className="clip-block__effect-drop" aria-hidden="true">{effectDropState === "allowed" ? tr("松开应用效果") : tr("不能应用到此片段")}</span> : null}
      <span
        className="clip-block__drag clip-block__drag--l"
        onPointerDown={(e) => {
          e.stopPropagation();
          if (!locked) onDragStart(e, "trim-l");
        }}
      />
      <span
        className="clip-block__drag clip-block__drag--r"
        onPointerDown={(e) => {
          e.stopPropagation();
          if (!locked) onDragStart(e, "trim-r");
        }}
      />
    </div>
  );
}
