/** 时间线跨轨拖拽系统（Timeline 持有，TrackRow 共享）。
    拖动中：全局跟踪指针 → 计算目标轨道 + 时间线位置 → 渲染幽灵条块。
    释放时一次提交 clip.move；类型/锁定错误在预览阶段拦截，重叠落点转为片段重排，
    后端校验仍作为最终安全网。 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { moveClip } from "../../store/clipEdit";
import { inferKind } from "../../lib/assetStore";
import { trackKindForMedia } from "../../lib/importMedia";
import { clipStartSecs, clipEndSecs, frameDurationSecs, secsToFrameRatString, snapSecsToFrame, toPx } from "./util";
import { SNAP_THRESHOLD_SECS, snapTo, collectSnapPoints } from "./snap";
import type { Clip, Track } from "../../types/api";
import { rationalToSecs } from "../../lib/rational";

export type DragMode = "move" | "trim-l" | "trim-r";

export interface ActiveDrag {
  mode: DragMode;
  clipId: string;
  srcTrackId: string;
  srcKind: string;
  requiredTrackKind: string;
  originStart: number;
  originEnd: number;
  grabOffsetPx: number;
}

export interface DropTarget {
  trackId: string;
  kind: string;
  startSecs: number;
  ghostLeft: number;
  ghostTop: number;
  widthPx: number;
  /** 重排时独立插入线的位置；幽灵条仍留在鼠标下方。 */
  insertLeft?: number;
  /** 是否发生了磁吸（ghost 高亮）。 */
  snapped: boolean;
  /** 落点被占用时，移动片段将作为完整片段插入相邻片段之间。 */
  reorderMode: boolean;
  /** 重排锚点与方向；后端据此在源片段移除后重新计算准确槽位。 */
  anchorClipId?: string;
  anchorPosition?: "before" | "after";
  /** 本次落点因主轨自动贴合转为插入顺序。 */
  mainTrackAutoFit?: boolean;
  /** 前端已知的不可放置原因；拖动时反馈并阻止提交。 */
  invalidReason?: string;
}

export interface TimelineDragApi {
  active: ActiveDrag | null;
  drop: DropTarget | null;
  begin: (e: React.PointerEvent, clip: Clip, track: Track, mode: DragMode, pxPerSec: number) => void;
  move: (e: React.PointerEvent) => void;
  finish: (e: React.PointerEvent) => void;
  cancel: () => void;
}

export function useTimelineDrag(): TimelineDragApi {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [active, setActive] = useState<ActiveDrag | null>(null);
  const [drop, setDrop] = useState<DropTarget | null>(null);
  const pointerIdRef = useRef<number | null>(null);
  const pendingRef = useRef<{ pointerId: number; drag: ActiveDrag; startX: number; startY: number } | null>(null);
  const pxPerSecRef = useRef(40);
  const activeRef = useRef<ActiveDrag | null>(null);
  const dropRef = useRef<DropTarget | null>(null);

  /** 计算指针下的目标（轨道 id + start 秒 + 幽灵定位），返回 null 表示不在轨道 lane 上。 */
  const computeDrop = useCallback((clientX: number, clientY: number): DropTarget | null => {
    const drag = activeRef.current;
    if (!drag) return null;
    const el = document.elementFromPoint(clientX, clientY);
    let lane = el?.closest?.(".timeline-track__lane") as HTMLElement | null;
    // 指针捕获后允许继续向左拖过固定轨道标题区。此时 elementFromPoint
    // 已不在 lane 内，但纵向仍属于该轨道，应钳制到时间线 0 点而不是丢失目标。
    if (!lane) {
      lane = Array.from(document.querySelectorAll<HTMLElement>(".timeline-track__lane"))
        .find((candidate) => {
          const bounds = candidate.getBoundingClientRect();
          return clientY >= bounds.top && clientY <= bounds.bottom;
        }) ?? null;
    }
    if (!lane) return null;
    const trackId = lane.getAttribute("data-track-id");
    const kind = lane.getAttribute("data-track-kind") || "video";
    if (!trackId) return null;
    const rect = lane.getBoundingClientRect();
    // lane 本身随横向滚动移动，rect.left 已包含 scrollLeft；不能再加一次滚动量。
    const px = Math.max(0, clientX - rect.left);
    // 保留按下时鼠标在片段内的位置，点击片段中心再拖时不会突然把片段左边缘吸到鼠标下。
    const st = getLatestState();
    const frameRate = st?.project?.sequence.fps;
    let startSecs = Math.max(0, snapSecsToFrame((px - drag.grabOffsetPx) / pxPerSecRef.current, frameRate));
    const durSecs = drag.originEnd - drag.originStart;
    const targetTrack = st?.project?.sequence.tracks.find((track) => track.id === trackId);
    const overlapsAt = (candidateStart: number) =>
      targetTrack?.clips.some((clip) => {
        if (clip.id === drag.clipId) return false;
        const otherStart = clipStartSecs(clip);
        const otherEnd = clipEndSecs(clip);
        return candidateStart < otherEnd - 1e-6 && candidateStart + durSecs > otherStart + 1e-6;
      }) ?? false;

    let invalidReason: string | undefined;
    if (kind !== drag.requiredTrackKind) {
      invalidReason = "素材类型与目标轨道不符";
    } else if (targetTrack?.locked) {
      invalidReason = "目标轨道已锁定，先解锁再移动片段";
    }

    // 磁吸：对 move 模式收集其它片段边缘 + 播放头
    let snapped = false;
    if (st?.project && drag.mode === "move" && st.snapEnabled && !invalidReason) {
      const allClips: { id: string; start: number; end: number }[] = [];
      for (const t of st.project.sequence.tracks) {
        for (const c of t.clips) {
          allClips.push({ id: c.id, start: clipStartSecs(c), end: clipEndSecs(c) });
        }
      }
      const pts = collectSnapPoints(
        allClips, drag.clipId, st.playhead,
        (st.project.sequence.markers || []).map((marker) => rationalToSecs(marker.time)),
      );
      // Keep magnetism easy to hit when zoomed out, but cap its visual radius
      // when zoomed in so a one-frame move is not pulled back to a clip edge.
      const snapThresholdSecs = Math.min(SNAP_THRESHOLD_SECS, 8 / pxPerSecRef.current);
      const unsnappedStart = startSecs;
      const snapStart = snapTo(unsnappedStart, pts, snapThresholdSecs);
      const snappedStart = Math.max(0, snapSecsToFrame(snapStart.value, frameRate));
      if (snapStart.snapped) {
        startSecs = snappedStart;
        snapped = true;
      }
      // 右缘也参与吸附：start + dur 的右缘尝试吸到其它右缘/播放头
      if (!snapped) {
        const endValue = unsnappedStart + durSecs;
        const snapEnd = snapTo(endValue, pts, snapThresholdSecs);
        const snappedEnd = Math.max(0, snapSecsToFrame(snapEnd.value - durSecs, frameRate));
        if (snapEnd.snapped) {
          startSecs = snappedEnd;
          snapped = true;
        }
      }
    }
    // 重排时幽灵条和插入槽位必须分离：幽灵始终跟随鼠标，插入线单独吸附。
    const ghostStartSecs = startSecs;
    const pointerSecs = Math.max(0, snapSecsToFrame(px / pxPerSecRef.current, frameRate));
    let reorderMode = false;
    let mainTrackAutoFit = false;
    let anchorClipId: string | undefined;
    let anchorPosition: "before" | "after" | undefined;
    const primaryVideoTrackId = st?.project?.sequence.tracks.find((track) => track.kind === "video")?.id;
    const autoFitOnMainTrack = Boolean(
      st?.mainTrackAutoFitEnabled
      && drag.requiredTrackKind === "video"
      && targetTrack?.id === primaryVideoTrackId,
    );
    const shouldInsert = overlapsAt(ghostStartSecs) || autoFitOnMainTrack;
    if (!invalidReason && shouldInsert && targetTrack) {
      const candidates = targetTrack.clips
        .filter((clip) => clip.id !== drag.clipId)
        .sort((a, b) => clipStartSecs(a) - clipStartSecs(b));
      if (candidates.length) {
        const hoveredClip = candidates.find(
          (clip) => pointerSecs >= clipStartSecs(clip) - 1e-6 && pointerSecs <= clipEndSecs(clip) + 1e-6,
        );
        const anchor = hoveredClip ?? candidates.reduce((nearest, clip) => {
          const nearestDistance = Math.abs(pointerSecs - (clipStartSecs(nearest) + clipEndSecs(nearest)) / 2);
          const distance = Math.abs(pointerSecs - (clipStartSecs(clip) + clipEndSecs(clip)) / 2);
          return distance < nearestDistance ? clip : nearest;
        });
        const anchorMidpoint = (clipStartSecs(anchor) + clipEndSecs(anchor)) / 2;
        anchorClipId = anchor.id;
        anchorPosition = pointerSecs < anchorMidpoint ? "before" : "after";
        startSecs = anchorPosition === "before" ? clipStartSecs(anchor) : clipEndSecs(anchor);
        reorderMode = true;
        mainTrackAutoFit = autoFitOnMainTrack && !overlapsAt(ghostStartSecs);
        snapped = true;
      }
    }

    const widthPx = Math.max(toPx(durSecs, pxPerSecRef.current), 8);
    // 幽灵 top：相对 timeline__content
    const content = lane.closest(".timeline__content") as HTMLElement | null;
    const ghostTop = content
      ? lane.getBoundingClientRect().top - content.getBoundingClientRect().top + (content.scrollTop || 0) + 6
      : 0;
    const ruler = content?.querySelector(".timeline-ruler") as HTMLElement | null;
    const originLeft = content && ruler
      ? ruler.getBoundingClientRect().left - content.getBoundingClientRect().left
      : 0;
    return {
      trackId,
      kind,
      startSecs,
      ghostLeft: originLeft + toPx(ghostStartSecs, pxPerSecRef.current),
      ghostTop,
      widthPx,
      insertLeft: reorderMode ? originLeft + toPx(startSecs, pxPerSecRef.current) : undefined,
      snapped,
      reorderMode,
      anchorClipId,
      anchorPosition,
      mainTrackAutoFit,
      invalidReason,
    };
  }, []);

  const begin = useCallback(
    (e: React.PointerEvent, clip: Clip, track: Track, mode: DragMode, pxPerSec: number) => {
      if (state.editLock) return;
      if (e.button !== 0) return;
      e.stopPropagation();
      pxPerSecRef.current = pxPerSec;
      const clipRect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      const inferredKind = inferKind(clip.assetRef.sourcePath, false, false);
      const stateObj: ActiveDrag = {
        mode,
        clipId: clip.id,
        srcTrackId: track.id,
        srcKind: track.kind,
        requiredTrackKind: inferredKind === "unknown"
          ? track.kind
          : trackKindForMedia(inferredKind),
        originStart: clipStartSecs(clip),
        originEnd: clipEndSecs(clip),
        grabOffsetPx: Math.max(0, Math.min(clipRect.width, e.clientX - clipRect.left)),
      };
      pointerIdRef.current = e.pointerId;
      pendingRef.current = { pointerId: e.pointerId, drag: stateObj, startX: e.clientX, startY: e.clientY };
      activeRef.current = null;
      dropRef.current = null;
      setActive(null);
      setDrop(null);
      try {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      // 初始定位
      const d0 = computeDrop(e.clientX, e.clientY);
      if (d0) {
        dropRef.current = d0;
        setDrop(d0);
      }
    },
    [computeDrop, state.editLock],
  );

  const move = useCallback(
    (e: React.PointerEvent) => {
      if (pointerIdRef.current !== e.pointerId) return;
      const pending = pendingRef.current;
      if (pending?.pointerId === e.pointerId) {
        if (Math.hypot(e.clientX - pending.startX, e.clientY - pending.startY) < 6) return;
        pendingRef.current = null;
        activeRef.current = pending.drag;
        setActive(pending.drag);
      }
      const drag = activeRef.current;
      if (!drag || drag.mode !== "move") return; // trim 仍走本地 overlay（不跨轨）
      e.preventDefault();
      const d = computeDrop(e.clientX, e.clientY);
      dropRef.current = d;
      setDrop(d);
    },
    [computeDrop],
  );

  const finish = useCallback(
    (e: React.PointerEvent) => {
      if (state.editLock) {
        pendingRef.current = null;
        pointerIdRef.current = null;
        activeRef.current = null;
        dropRef.current = null;
        setActive(null);
        setDrop(null);
        return;
      }
      const drag = activeRef.current;
      if (pointerIdRef.current !== e.pointerId) return;
      // 指针未越过拖动阈值时，这只是一次普通点击；让 React 的 click 处理选择/切割。
      if (!drag) {
        pendingRef.current = null;
        pointerIdRef.current = null;
        dropRef.current = null;
        setDrop(null);
        return;
      }
      const d = drag.mode === "move" ? computeDrop(e.clientX, e.clientY) : null;
      pendingRef.current = null;
      pointerIdRef.current = null;
      activeRef.current = null;
      setActive(null);
      dropRef.current = null;
      setDrop(null);
      if (drag.mode !== "move") return;
      if (!d) {
        dispatch({
          type: "STATUS_SET",
          severity: "warn",
          text: "请在时间线轨道区域内放置片段；需要更多空间时可收起右侧属性面板",
        });
        return;
      }

      const st = getLatestState();
      if (!st || !st.currentId) return;
      const targetChanged = d.trackId !== drag.srcTrackId;
      if (d.invalidReason) {
        dispatch({ type: "STATUS_SET", severity: "warn", text: d.invalidReason });
        return;
      }
      // Safety net for a stale target track while the pointer was captured.
      if (targetChanged && d.kind !== drag.requiredTrackKind) {
        dispatch({ type: "STATUS_SET", severity: "warn", text: "素材类型与目标轨道不符" });
        return;
      }
      const newStart = d.startSecs;
      const frameRate = st.project?.sequence.fps;
      if (!targetChanged && Math.abs(newStart - drag.originStart) < frameDurationSecs(frameRate) / 2) return;
      const timelineStart = secsToFrameRatString(newStart, frameRate);
      void moveClip(dispatch, st, {
        clipId: drag.clipId,
        followAttachments: !e.altKey,
        ...(targetChanged ? { trackId: d.trackId, timelineStart } : { timelineStart }),
        ...(d.reorderMode && d.anchorClipId && d.anchorPosition
          ? {
              mode: "reorder" as const,
              anchorClipId: d.anchorClipId,
              anchorPosition: d.anchorPosition,
            }
          : {}),
      });
    },
    [dispatch, computeDrop, state.editLock],
  );

  const cancel = useCallback(() => {
    pendingRef.current = null;
    pointerIdRef.current = null;
    activeRef.current = null;
    dropRef.current = null;
    setActive(null);
    setDrop(null);
  }, []);

  // If an Agent acquires the project lease mid-gesture, drop the pointer-captured
  // operation locally. The pointer-up must not send a command under the new lease.
  useEffect(() => {
    if (state.editLock) cancel();
  }, [state.editLock, cancel]);

  return useMemo(() => ({ active, drop, begin, move, finish, cancel }), [active, drop, begin, move, finish, cancel]);
}
