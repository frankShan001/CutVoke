import { useLayoutEffect, useMemo, useState, type RefObject } from "react";

export interface TimelineViewport { start: number; end: number }

/** Time range with a pixel overscan; lane geometry and scrolling stay unchanged. */
export function useTimelineViewport(root: RefObject<HTMLElement | null>, pxPerSec: number): TimelineViewport {
  const [pixels, setPixels] = useState({ start: 0, end: window.innerWidth + 512 });
  useLayoutEffect(() => {
    const scroller = root.current?.querySelector<HTMLElement>(".timeline__scroller");
    if (!scroller) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const start = Math.max(0, Math.floor((scroller.scrollLeft - 256) / 128) * 128);
      const end = Math.ceil((scroller.scrollLeft + scroller.clientWidth + 256) / 128) * 128;
      setPixels((current) => current.start === start && current.end === end ? current : { start, end });
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    const resize = new ResizeObserver(schedule);
    resize.observe(scroller);
    scroller.addEventListener("scroll", schedule, { passive: true });
    update();
    return () => { cancelAnimationFrame(frame); resize.disconnect(); scroller.removeEventListener("scroll", schedule); };
  }, [root]);
  // A memoized object lets clip rows skip every playback tick.
  return useMemo(() => ({ start: pixels.start / pxPerSec, end: pixels.end / pxPerSec }), [pixels, pxPerSec]);
}
