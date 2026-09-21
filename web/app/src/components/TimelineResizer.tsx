/** 时间线高度拖拽分隔条（剪映式）：上下拖动改变底部时间线高度。
    用 CSS 变量 --timeline-h 控制，范围 120px ~ 70% 容器高。 */

import { useCallback, useEffect, useRef, useState } from "react";

export function TimelineResizer({ containerRef }: { containerRef: React.RefObject<HTMLDivElement | null> }) {
  const [dragging, setDragging] = useState(false);
  const pointerId = useRef<number | null>(null);
  const startY = useRef(0);
  const startH = useRef(300);

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    pointerId.current = e.pointerId;
    startY.current = e.clientY;
    startH.current = !Number.isNaN(parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--timeline-h")))
      ? parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--timeline-h"))
      : 300;
    setDragging(true);
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  const onPointerMove = useCallback(
    (e: PointerEvent) => {
      if (pointerId.current !== e.pointerId) return;
      const dy = startY.current - e.clientY; // 向上拖 = 增大时间线
      const maxH = containerRef.current?.clientHeight ? containerRef.current.clientHeight * 0.7 : 500;
      const next = Math.min(maxH, Math.max(120, startH.current + dy));
      document.documentElement.style.setProperty("--timeline-h", `${Math.round(next)}px`);
    },
    [containerRef],
  );

  const onPointerUp = useCallback((e: PointerEvent) => {
    if (pointerId.current !== e.pointerId) return;
    pointerId.current = null;
    setDragging(false);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [dragging, onPointerMove, onPointerUp]);

  return (
    <div
      className={`tl-resizer ${dragging ? "tl-resizer--active" : ""}`}
      onPointerDown={onPointerDown}
      role="separator"
      aria-orientation="horizontal"
      aria-label="拖动调整时间线高度"
      title="拖动调整时间线高度"
    >
      <span className="tl-resizer__grip" />
    </div>
  );
}