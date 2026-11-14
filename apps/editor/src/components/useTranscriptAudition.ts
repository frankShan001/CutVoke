import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { AUDITION_RANGE_EVENT, CANCEL_AUDITION_EVENT, STOP_AUDITION_AUDIO_EVENT, type TranscriptAudition } from "../lib/transcript";

interface Options {
  projectId: string | null; sequenceId?: string; revision: string;
  end: number; playhead: number; scrubbing: boolean;
  video: RefObject<HTMLVideoElement | null>;
  windowStart: number;
  clock: (initialize?: boolean) => { time: number; playing: boolean } | null;
  deadlinePosition?: () => number | null;
  seekVersion: number;
  play: () => void;
  pause: () => void;
  seek: (time: number) => void;
  onEnd?: () => void;
}

/** The player's actual transport owns the stop boundary, including the native audio mix. */
export function useTranscriptAudition(options: Options) {
  const latest = useRef(options);
  latest.current = options;
  const active = useRef<TranscriptAudition | null>(null);
  const frame = useRef(0);
  const deadline = useRef(0);
  const stop = (end?: number) => {
    if (!active.current) return;
    active.current = null;
    cancelAnimationFrame(frame.current);
    window.clearTimeout(deadline.current);
    deadline.current = 0;
    latest.current.pause();
    // Native audio voices are detached elements. Pause synchronously even when
    // the tab is hidden and their normal animation-frame update is suspended.
    window.dispatchEvent(new CustomEvent(STOP_AUDITION_AUDIO_EVENT, { detail: { time: end } }));
    if (end !== undefined) { latest.current.seek(end); latest.current.onEnd?.(); }
  };
  // Every transport consumer checks this before publishing/using a clock.
  // A late paint must not publish an overrun and then restart paused media.
  const stopAtBoundary = (time: number): number | null => {
    const target = active.current, now = latest.current;
    if (!target || now.projectId !== target.projectId || now.sequenceId !== target.sequenceId || now.revision !== target.revision) return null;
    if (time < target.end) return null;
    const end = target.end;
    stop(end);
    return end;
  };
  // Seeking or scrubbing deliberately ends an audition, including seeks inside its range.
  useLayoutEffect(() => {
    if (!active.current) return;
    if (options.scrubbing) stop();
  }, [options.scrubbing]);
  useLayoutEffect(() => {
    if (active.current) stop();
  }, [options.seekVersion]);
  useEffect(() => {
    const begin = (event: Event) => {
      const range = (event as CustomEvent<TranscriptAudition>).detail;
      const current = latest.current;
      if (!range || range.projectId !== current.projectId || range.sequenceId !== current.sequenceId || range.revision !== current.revision ||
        !Number.isFinite(range.start) || !Number.isFinite(range.end) || range.start < 0 || range.end <= range.start || range.start >= current.end) return;
      stop(); current.pause();
      active.current = { ...range, end: Math.min(current.end, range.end) };
      current.seek(range.start);
      // Let the seek render before starting the normal player transport.
      frame.current = requestAnimationFrame(() => {
        const target = active.current;
        if (!target) return;
        const now = latest.current;
        if (now.projectId !== target.projectId || now.sequenceId !== target.sequenceId || now.revision !== target.revision) {
          // The old transport was already paused at begin(). Do not run any
          // callbacks belonging to the newly rendered editor before cleanup.
          active.current = null;
          return;
        }
        if (now.scrubbing || document.hidden) { stop(); return; }
        now.play();
        // Audio keeps running when RAF callbacks are delayed. Arm an
        // independent timer from the actual media clock, not elapsed wall
        // time: a suspended device or buffering video must not lose its range.
        const checkDeadline = () => {
          const target = active.current;
          if (!target) return;
          const now = latest.current;
          if (now.projectId !== target.projectId || now.sequenceId !== target.sequenceId || now.revision !== target.revision) { stop(); return; }
          const clock = now.clock(false), video = now.video.current;
          if (!active.current) return; // The shared clock may have stopped us.
          const time = clock?.time ?? (video && !video.paused ? now.windowStart + video.currentTime : now.playhead);
          const nativeTime = now.deadlinePosition?.();
          const deadlineTime = nativeTime != null ? Math.max(time, nativeTime) : time;
          if (stopAtBoundary(deadlineTime) !== null) return;
          deadline.current = window.setTimeout(checkDeadline,
            clock?.playing || nativeTime != null ? Math.max(1, (target.end - deadlineTime) * 1000) : 25);
        };
        checkDeadline();
        if (!active.current) return;
        const tick = () => {
          const target = active.current;
          if (!target) return;
          const now = latest.current;
          if (now.projectId !== target.projectId || now.sequenceId !== target.sequenceId || now.revision !== target.revision) {
            stop(); return;
          }
          const clock = now.clock();
          const video = now.video.current;
          const time = clock?.time ?? (video && !video.paused ? now.windowStart + video.currentTime : now.playhead);
          if (time >= target.end) { stop(target.end); return; }
          if (time < target.start - .1 || now.scrubbing) { stop(); return; }
          frame.current = requestAnimationFrame(tick);
        };
        frame.current = requestAnimationFrame(tick);
      });
    };
    const cancel = () => stop();
    const onVisibility = () => { if (document.hidden) stop(); };
    window.addEventListener(AUDITION_RANGE_EVENT, begin);
    window.addEventListener(CANCEL_AUDITION_EVENT, cancel);
    window.addEventListener("cutvoke:pause-play", cancel);
    window.addEventListener("cutvoke:pause-for-edit", cancel);
    window.addEventListener("blur", cancel);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      window.removeEventListener(AUDITION_RANGE_EVENT, begin);
      window.removeEventListener(CANCEL_AUDITION_EVENT, cancel);
      window.removeEventListener("cutvoke:pause-play", cancel);
      window.removeEventListener("cutvoke:pause-for-edit", cancel);
      window.removeEventListener("blur", cancel);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [options.projectId, options.sequenceId, options.revision]);
  return stopAtBoundary;
}
