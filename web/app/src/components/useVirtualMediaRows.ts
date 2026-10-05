import { useLayoutEffect, useRef, useState } from "react";

export const MEDIA_ROW_HEIGHT = 52;
const STRIDE = MEDIA_ROW_HEIGHT + 6;

/** Virtualize in the existing sidebar scroller, keeping one continuous list. */
export function useVirtualMediaRows(count: number, active: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState({ start: 0, end: 20 });
  const virtual = count > 100;
  useLayoutEffect(() => {
    const list = ref.current;
    const scroller = list?.closest<HTMLElement>(".zone-left__scroll");
    if (!active || !virtual || !list || !scroller) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const top = scroller.getBoundingClientRect().top - list.getBoundingClientRect().top;
      const start = Math.max(0, Math.min(count - 1, Math.floor(top / STRIDE) - 5));
      const end = Math.min(count, Math.max(start + 1, Math.ceil((top + scroller.clientHeight) / STRIDE) + 5));
      setRange((current) => current.start === start && current.end === end ? current : { start, end });
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    const resize = new ResizeObserver(schedule);
    resize.observe(scroller);
    resize.observe(list);
    scroller.addEventListener("scroll", schedule, { passive: true });
    update();
    return () => { cancelAnimationFrame(frame); resize.disconnect(); scroller.removeEventListener("scroll", schedule); };
  }, [count, active, virtual]);
  const start = virtual ? Math.min(range.start, Math.max(0, count - 1)) : 0;
  const end = virtual ? Math.min(count, Math.max(start + 1, range.end)) : count;
  return { ref, virtual, start, end, before: start * STRIDE, after: (count - end) * STRIDE };
}
