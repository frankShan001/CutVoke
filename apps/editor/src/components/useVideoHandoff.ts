import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";

/** Keep the outgoing decoded picture across a consecutive playback-window swap. */
export function useVideoHandoff(opts: {
  videoRef: RefObject<HTMLVideoElement | null>;
  identity: string;
  source: string;
  active: boolean;
  playing: boolean;
  onPresented: (source: string) => void;
}) {
  const latest = useRef(opts);
  latest.current = opts;
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pending = useRef<{ kind: "video" | "paint"; cancel: () => void } | null>(null);
  const [holdingIdentity, setHoldingIdentity] = useState<string | null>(null);

  const clearHold = useCallback(() => {
    if (canvasRef.current) canvasRef.current.style.visibility = "hidden";
    setHoldingIdentity(null);
  }, []);

  const confirmFrame = useCallback((video: HTMLVideoElement) => {
    if (video !== latest.current.videoRef.current) return;
    // A paused video covered by the retained canvas may not be submitted to
    // the compositor. Once decoded, let it paint without waiting for a new
    // playing frame; cancellation must also complete the loading state.
    const pausedDecoded = video.paused && video.readyState >= 2 && !video.seeking;
    if (pending.current) {
      if (!pausedDecoded || pending.current.kind === "paint") return;
      pending.current.cancel();
      pending.current = null;
    }
    const presented = () => {
      pending.current = null;
      if (video !== latest.current.videoRef.current || !latest.current.active) return;
      if (video.seeking || video.readyState < 2) {
        confirmFrame(video);
        return;
      }
      clearHold();
      latest.current.onPresented(video.dataset.previewSource || video.getAttribute("src") || "");
    };
    if (typeof video.requestVideoFrameCallback === "function" && !pausedDecoded) {
      const id = video.requestVideoFrameCallback(presented);
      pending.current = { kind: "video", cancel: () => video.cancelVideoFrameCallback(id) };
    } else if (video.readyState >= 2 && !video.seeking) {
      let id = requestAnimationFrame(() => { id = requestAnimationFrame(presented); });
      pending.current = { kind: "paint", cancel: () => cancelAnimationFrame(id) };
    }
  }, [clearHold]);

  const bindVideo = useCallback((video: HTMLVideoElement | null) => {
    if (video === latest.current.videoRef.current) {
      if (video) confirmFrame(video);
      return;
    }
    pending.current?.cancel();
    pending.current = null;
    const { videoRef, identity, active } = latest.current;
    const previous = videoRef.current;
    const canvas = canvasRef.current;
    if (previous && canvas) {
      if (active && previous.readyState >= 2 &&
          previous.dataset.previewIdentity === identity) {
        try {
          canvas.width = Math.min(960, previous.videoWidth);
          canvas.height = Math.max(1, Math.round(canvas.width * previous.videoHeight / previous.videoWidth));
          const context = canvas.getContext("2d");
          if (!context) throw new Error("Preview canvas is unavailable");
          context.drawImage(previous, 0, 0, canvas.width, canvas.height);
          canvas.dataset.previewIdentity = identity;
          canvas.style.visibility = "visible";
          setHoldingIdentity(identity);
        } catch {
          clearHold();
        }
      } else {
        clearHold();
      }
    }
    videoRef.current = video;
    if (video) confirmFrame(video);
  }, [clearHold, confirmFrame]);

  useLayoutEffect(() => {
    if (!opts.active || canvasRef.current?.dataset.previewIdentity !== opts.identity) clearHold();
  }, [opts.active, opts.identity, clearHold]);

  return { canvasRef, bindVideo, confirmFrame, holding: holdingIdentity === opts.identity && opts.active };
}
