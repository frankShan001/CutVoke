/** 时间线跨轨拖拽系统（Timeline 持有，TrackRow 共享）。
    拖动中：全局跟踪指针 → 计算目标轨道 + 时间线位置 → 渲染幽灵条块。
    释放时：一次提交 clip.move（同轨带 timelineStart；跨轨带 trackId + timelineStart）。
    kind 不一致 / 重叠由后端 400 兜底；前端对 kind 不一致静默跳过。 */

import { useCallback, useRef, useState } from "react";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { moveClip } from "../../store/clipEdit";
import { clipStartSecs, clipEndSecs, secsToRatString, toPx } from "./util";
import { snapTo, collectSnapPoints } from "./snap";
import type { Clip, Track } from "../../types/api";

export type DragMode = "move" | "trim-l" | "trim-r";

export interface ActiveDrag {
  mode: DragMode;
  clipId: string;
  srcTrackId: string;
  srcKind: string;
  originStart: number;
  originEnd: number;
}

export interface DropTarget {
  trackId: string;
  kind: string;
  startSecs: number;
  ghostLeft: number;
  ghostTop: number;
  widthPx: number;
  /** 是否发生了磁吸（ghost 高亮）。 */
  snapped: boolean;
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
  const { dispatch } = useEditor();
  const [active, setActive] = useState<ActiveDrag | null>(null);
  const [drop, setDrop] = useState<DropTarget | null>(null);
  const pointerIdRef = useRef<number | null>(null);
  const pxPerSecRef = useRef(40);
  const activeRef = useRef<ActiveDrag | null>(null);
  const dropRef = useRef<DropTarget | null>(null);

  const LABEL_W = 190;

  /** 计算指针下的目标（轨道 id + start 秒 + 幽灵定位），返回 null 表示不在轨道 lane 上。 */
  const computeDrop = useCallback((clientX: number, clientY: number): DropTarget | null => {
    const drag = activeRef.current;
    if (!drag) return null;
    const el = document.elementFromPoint(clientX, clientY);
    const lane = el?.closest?.(".timeline-track__lane") as HTMLElement | null;
    if (!lane) return null;
    const trackId = lane.getAttribute("data-track-id");
    const kind = lane.getAttribute("data-track-kind") || "video";
    if (!trackId) return null;
    const rect = lane.getBoundingClientRect();
    const scroller = lane.closest(".timeline__scroller") as HTMLElement | null;
    const scrollLeft = scroller ? scroller.scrollLeft : 0;
    const px = clientX - rect.left + scrollLeft;
    let startSecs = Math.max(0, Math.round((px / pxPerSecRef.current) * 10) / 10);
    const durSecs = drag.originEnd - drag.originStart;

    // 磁吸：对 move 模式收集其它片段边缘 + 播放头
    let snapped = false;
    const st = getLatestState();
    if (st?.project && drag.mode === "move" && st.snapEnabled) {
      const allClips: { id: string; start: number; end: number }[] = [];
      for (const t of st.project.sequence.tracks) {
        for (const c of t.clips) {
          allClips.push({ id: c.id, start: clipStartSecs(c), end: clipEndSecs(c) });
        }
      }
      const pts = collectSnapPoints(allClips, drag.clipId, st.playhead);
      const snapStart = snapTo(startSecs, pts);
      if (snapStart.snapped) {
        startSecs = Math.max(0, snapStart.value);
        snapped = true;
      }
      // 右缘也参与吸附：start + dur 的右缘尝试吸到其它右缘/播放头
      if (!snapped) {
        const endValue = startSecs + durSecs;
        const snapEnd = snapTo(endValue, pts);
        if (snapEnd.snapped) {
          startSecs = Math.max(0, snapEnd.value - durSecs);
          snapped = true;
        }
      }
    }

    const widthPx = Math.max(toPx(durSecs, pxPerSecRef.current), 8);
    // 幽灵 top：相对 timeline__content
    const content = lane.closest(".timeline__content") as HTMLElement | null;
    const ghostTop = content ? lane.getBoundingClientRect().top - content.getBoundingClientRect().top + (content.scrollTop || 0) : 0;
    return {
      trackId,
      kind,
      startSecs,
      ghostLeft: LABEL_W + toPx(startSecs, pxPerSecRef.current),
      ghostTop,
      widthPx,
      snapped,
    };
  }, []);

  const begin = useCallback(
    (e: React.PointerEvent, clip: Clip, track: Track, mode: DragMode, pxPerSec: number) => {
      if (e.button !== 0 || e.button === undefined) return;
      if (mode !== "move" && e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      pxPerSecRef.current = pxPerSec;
      const stateObj: ActiveDrag = {
        mode,
        clipId: clip.id,
        srcTrackId: track.id,
        srcKind: track.kind,
        originStart: clipStartSecs(clip),
        originEnd: clipEndSecs(clip),
      };
      activeRef.current = stateObj;
      setActive(stateObj);
      pointerIdRef.current = e.pointerId;
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
    [computeDrop],
  );

  const move = useCallback(
    (e: React.PointerEvent) => {
      const drag = activeRef.current;
      if (!drag || pointerIdRef.current !== e.pointerId) return;
      if (drag.mode !== "move") return; // trim 仍走本地 overlay（不跨轨）
      const d = computeDrop(e.clientX, e.clientY);
      if (d) {
        dropRef.current = d;
        setDrop(d);
      }
    },
    [computeDrop],
  );

  const finish = useCallback(
    (e: React.PointerEvent) => {
      const drag = activeRef.current;
      if (!drag || pointerIdRef.current !== e.pointerId) return;
      pointerIdRef.current = null;
      activeRef.current = null;
      setActive(null);
      const d = dropRef.current;
      dropRef.current = null;
      setDrop(null);
      if (drag.mode !== "move" || !d) return;

      const st = getLatestState();
      if (!st || !st.currentId) return;
      const targetChanged = d.trackId !== drag.srcTrackId;
      // kind 不一致：静默不做
      if (targetChanged && d.kind !== drag.srcKind) return;
      const newStart = d.startSecs;
      if (!targetChanged && Math.abs(newStart - drag.originStart) < 0.05) return;
      void moveClip(dispatch, st, {
        clipId: drag.clipId,
        ...(targetChanged ? { trackId: d.trackId, timelineStart: secsToRatString(newStart) } : { timelineStart: secsToRatString(newStart) }),
      });
    },
    [dispatch],
  );

  const cancel = useCallback(() => {
    pointerIdRef.current = null;
    activeRef.current = null;
    dropRef.current = null;
    setActive(null);
    setDrop(null);
  }, []);

  return { active, drop, begin, move, finish, cancel };
}