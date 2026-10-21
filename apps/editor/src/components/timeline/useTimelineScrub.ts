import { useEffect, useRef, type PointerEvent } from "react";
import { useEditor } from "../../store/editor";

/** One coordinate system for ruler/playhead scrubbing, including edge scrolling. */
export function useTimelineScrub(pxPerSec: number, maxSecs: number) {
  const { dispatch } = useEditor({ subscribeToClock: false });
  const active = useRef<{ pointerId: number; clientX: number; target: HTMLElement } | null>(null);
  const frame = useRef<number | null>(null);
  const limits = useRef({ pxPerSec, maxSecs });
  limits.current = { pxPerSec, maxSecs };

  const seek = (clientX: number, target: HTMLElement) => {
    const ruler = target.closest(".timeline__content")?.querySelector(".timeline-ruler");
    if (!ruler) return;
    const { pxPerSec: scale, maxSecs: end } = limits.current;
    dispatch({ type: "PLAYHEAD_SET", t: Math.max(0, Math.min(end, (clientX - ruler.getBoundingClientRect().left) / scale)) });
  };

  const stop = () => {
    if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    frame.current = null;
    if (active.current) {
      active.current = null;
      dispatch({ type: "SCRUBBING_SET", active: false });
    }
  };
  useEffect(() => {
    const release = (event: MouseEvent) => {
      const drag=active.current;
      if (!drag || event.button!==0) return;
      seek(event.clientX,drag.target);
      stop();
    };
    // Native pointer capture may be lost when the preview mounts a new layer.
    // Always end the gesture on the window's physical mouse/pointer release.
    window.addEventListener("pointerup",release,true);
    window.addEventListener("mouseup",release,true);
    window.addEventListener("pointercancel",stop,true);
    window.addEventListener("blur", stop);
    return () => {
      window.removeEventListener("pointerup",release,true);
      window.removeEventListener("mouseup",release,true);
      window.removeEventListener("pointercancel",stop,true);
      window.removeEventListener("blur", stop);
      stop();
    };
  }, [dispatch]);

  const onPointerDown = (event: PointerEvent<HTMLElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    stop();
    const target = event.currentTarget;
    active.current = { pointerId: event.pointerId, clientX: event.clientX, target };
    dispatch({ type: "SCRUBBING_SET", active: true });
    if (target.tabIndex >= 0) target.focus({ preventScroll: true });
    target.setPointerCapture(event.pointerId);
    seek(event.clientX, target);

    let previous = performance.now();
    const tick = (now: number) => {
      const drag = active.current;
      if (!drag) return;
      const dt = Math.min(32, Math.max(0, now - previous)) / 1000;
      previous = now;
      const scroller = drag.target.closest<HTMLElement>(".timeline__scroller");
      const ruler = scroller?.querySelector<HTMLElement>(".timeline-ruler");
      const label = scroller?.querySelector<HTMLElement>(".timeline-track__label");
      if (scroller && ruler) {
        const rect = scroller.getBoundingClientRect();
        // The fixed label column is not part of the seekable viewport.
        const left = label ? label.getBoundingClientRect().right + 8 : rect.left;
        const right = rect.left + scroller.clientWidth;
        const zone = Math.min(56, Math.max(16, (right - left) / 4));
        const velocity = drag.clientX > right - zone
          ? Math.min(1, (drag.clientX - right + zone) / zone)
          : drag.clientX < left + zone ? -Math.min(1, (left + zone - drag.clientX) / zone) : 0;
        const origin = ruler.getBoundingClientRect().left - rect.left + scroller.scrollLeft;
        // Stop at the content end rather than continuing into the blank tail.
        const endScroll = Math.max(0, Math.ceil(origin + limits.current.maxSecs * limits.current.pxPerSec - (Math.min(right, drag.clientX) - rect.left)));
        const before = scroller.scrollLeft;
        if (velocity > 0) scroller.scrollLeft = before + Math.min(Math.max(0, endScroll - before), velocity * 180 * dt);
        else if (velocity < 0) scroller.scrollLeft = Math.max(0, before + velocity * 180 * dt);
        if (scroller.scrollLeft !== before) seek(drag.clientX, drag.target);
      }
      frame.current = window.requestAnimationFrame(tick);
    };
    frame.current = window.requestAnimationFrame(tick);
  };

  const onPointerMove = (event: PointerEvent<HTMLElement>) => {
    if (active.current?.pointerId !== event.pointerId) return;
    active.current.clientX = event.clientX;
    seek(event.clientX, event.currentTarget);
  };
  const finish = (event: PointerEvent<HTMLElement>) => {
    if (active.current?.pointerId !== event.pointerId) return;
    if (event.type === "pointerup") seek(event.clientX, event.currentTarget);
    stop();
  };
  return { onPointerDown, onPointerMove, onPointerUp: finish, onPointerCancel: finish, onLostPointerCapture: finish };
}
