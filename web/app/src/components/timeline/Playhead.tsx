/** 播放头：时间线竖线 + 顶部可拖游标。拖动更新全局 playhead（秒）。
    定位：相对 .timeline__content；x 偏移 = 左侧轨道标签宽 + 播放头像素。 */

import { useEffect, useRef } from "react";
import { useEditor } from "../../store/editor";
import { toPx } from "./util";

export function Playhead({ pxPerSec, maxSecs }: { pxPerSec: number; maxSecs: number }) {
  const { state, dispatch } = useEditor();
  const dragging = useRef(false);
  useEffect(() => () => {
    if (dragging.current) dispatch({ type: "SCRUBBING_SET", active: false });
  }, [dispatch]);

  const playheadSecs = Math.max(0, Math.min(maxSecs, state.playhead));
  const left = `calc(var(--timeline-content-inset-x, 12px) + var(--track-label-w) + var(--space-2) + ${toPx(playheadSecs, pxPerSec)}px)`;

  const moveTo = (clientX: number, target: HTMLElement) => {
    const content = target.closest(".timeline__content") as HTMLElement | null;
    if (!content) return;
    const ruler = content.querySelector(".timeline-ruler");
    if (!ruler) return;
    const rect = ruler.getBoundingClientRect();
    const secs = Math.max(0, Math.min(maxSecs, (clientX - rect.left) / pxPerSec));
    dispatch({ type: "PLAYHEAD_SET", t: secs });
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    dragging.current = true;
    dispatch({ type: "SCRUBBING_SET", active: true });
    const target = e.currentTarget as HTMLElement;
    try {
      target.setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    moveTo(e.clientX, target);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    moveTo(e.clientX, e.currentTarget as HTMLElement);
  };
  const onUp = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    if (e.type === "pointerup") moveTo(e.clientX, e.currentTarget as HTMLElement);
    dragging.current = false;
    dispatch({ type: "SCRUBBING_SET", active: false });
  };

  return (
    <div className="timeline-playhead" style={{ left }} aria-hidden="true">
      <div
        className="timeline-playhead__drag"
        onPointerDown={onPointerDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        onLostPointerCapture={onUp}
      />
      <span className="timeline-playhead__head" />
    </div>
  );
}
