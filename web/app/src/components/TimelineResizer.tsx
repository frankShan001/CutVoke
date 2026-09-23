/** 时间线高度拖拽分隔条（剪映式）：上下拖动改变底部时间线高度。
    用 CSS 变量 --timeline-h 控制，范围 120px ~ 70% 容器高。 */

import { useCallback, useEffect, useRef, useState } from "react";

function readTimelineHeight() {
  if (typeof window === "undefined") return 300;
  const value = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--timeline-h"));
  return Number.isFinite(value) ? value : 300;
}

export function TimelineResizer({ containerRef }: { containerRef: React.RefObject<HTMLDivElement | null> }) {
  const [dragging, setDragging] = useState(false);
  const [height, setHeight] = useState(readTimelineHeight);
  const [containerHeight, setContainerHeight] = useState(() => containerRef.current?.clientHeight || 500);
  const pointerId = useRef<number | null>(null);
  const startY = useRef(0);
  const startH = useRef(300);

  const maxHeight = Math.max(120, Math.round(containerHeight * 0.7));
  const applyHeight = (value: number) => {
    const next = Math.round(Math.min(maxHeight, Math.max(120, value)));
    document.documentElement.style.setProperty("--timeline-h", `${next}px`);
    setHeight(next);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    pointerId.current = e.pointerId;
    startY.current = e.clientY;
    startH.current = readTimelineHeight();
    setHeight(startH.current);
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
      const rounded = Math.round(next);
      document.documentElement.style.setProperty("--timeline-h", `${rounded}px`);
      setHeight(rounded);
    },
    [containerRef],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const steps: Record<string, number> = {
      ArrowUp: 16,
      ArrowDown: -16,
      PageUp: 64,
      PageDown: -64,
      Home: -Infinity,
      End: Infinity,
    };
    if (!(e.key in steps)) return;
    e.preventDefault();
    const step = steps[e.key];
    applyHeight(Number.isFinite(step) ? readTimelineHeight() + step : step < 0 ? 120 : maxHeight);
  };

  useEffect(() => {
    const syncLayout = () => {
      setContainerHeight(containerRef.current?.clientHeight || 500);
      // Keep CSS breakpoint defaults and the accessible value in sync until the user sets a height.
      if (!document.documentElement.style.getPropertyValue("--timeline-h").trim()) {
        setHeight(readTimelineHeight());
      }
    };
    window.addEventListener("resize", syncLayout);
    return () => window.removeEventListener("resize", syncLayout);
  }, [containerRef]);

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
      onKeyDown={onKeyDown}
      role="separator"
      aria-orientation="horizontal"
      aria-label="拖动调整时间线高度"
      aria-valuemin={120}
      aria-valuemax={maxHeight}
      aria-valuenow={Math.round(height)}
      aria-valuetext={`时间线高度 ${Math.round(height)} 像素`}
      tabIndex={0}
      title="拖动调整时间线高度；方向键微调，Page Up/Down 快速调整"
    >
      <span className="tl-resizer__grip" />
    </div>
  );
}
