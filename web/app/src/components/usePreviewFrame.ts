/** 单帧降级：一次只渲染一帧，队列只保留最新位置；短暂失败自动重试。 */

import { useCallback, useEffect, useRef, useState, type Dispatch, type RefObject } from "react";
import { fetchPreviewFrame } from "../lib/mediaApi";
import type { EditorAction } from "../store/editor";

export type FrameState = "loading" | "ok" | "empty" | "error";

const PREVIEW_W = 640;
const PREVIEW_H = 360;

export function usePreviewFrame(opts: {
  projectId: string | null;
  active: boolean;
  playing: boolean;
  totalSecs: number;
  playheadRef: RefObject<number>;
  dispatchRef: RefObject<Dispatch<EditorAction>>;
  onEnded: () => void;
}) {
  const { projectId, active, playing, totalSecs, playheadRef, dispatchRef, onEnded } = opts;
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [frameState, setFrameState] = useState<FrameState>("loading");
  const [frameError, setFrameError] = useState<string | null>(null);
  const seqRef = useRef(0);
  const lastReq = useRef(0);
  const queuedTargetRef = useRef<{ pid: string; t: number; attempt: number; notBefore: number } | null>(null);
  const inFlightRef = useRef<{ controller: AbortController; pid: string; t: number } | null>(null);
  const pumpRef = useRef<() => void>(() => {});
  const playingRef = useRef(playing);
  playingRef.current = playing;
  const timerRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const frameUrlRef = useRef<string | null>(null);

  const clearFrame = useCallback(() => {
    setFrameUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      frameUrlRef.current = null;
      return null;
    });
  }, []);

  const cancelQueued = useCallback(() => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    queuedTargetRef.current = null;
    seqRef.current++;
    inFlightRef.current?.controller.abort();
    inFlightRef.current = null;
  }, []);

  pumpRef.current = () => {
    if (inFlightRef.current || timerRef.current != null) return;
    const target = queuedTargetRef.current;
    if (!target) return;
    const delay = Math.max(0, lastReq.current + 300 - Date.now(), target.notBefore - Date.now());
    if (delay > 0) {
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        pumpRef.current();
      }, delay);
      return;
    }
    queuedTargetRef.current = null;
    const { pid, t, attempt } = target;
    const controller = new AbortController();
    inFlightRef.current = { controller, pid, t };
    lastReq.current = Date.now();
    const seq = ++seqRef.current;
    setFrameState((previous) => previous === "error" || previous === "empty"
      ? "loading" : previous);
    setFrameError(null);
    void fetchPreviewFrame({ projectId: pid, t, width: PREVIEW_W, height: PREVIEW_H,
      signal: controller.signal }).then((res) => {
      const next = queuedTargetRef.current;
      const outdated = !!next && (next.pid !== pid || Math.abs(next.t - t) > 0.001);
      if (seq !== seqRef.current || controller.signal.aborted || (outdated && !playingRef.current)) {
        if (res.kind === "frame") URL.revokeObjectURL(res.url);
        return;
      }
      if (res.kind === "frame") {
        setFrameUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          frameUrlRef.current = res.url;
          return res.url;
        });
        setFrameState("ok");
        setFrameError(null);
      } else if (res.kind === "empty") {
        clearFrame();
        setFrameState("empty");
        setFrameError(null);
      } else if (res.kind === "error" && !outdated) {
        if (res.retryable && attempt < 2) {
          queuedTargetRef.current = { pid, t, attempt: attempt + 1,
            notBefore: Date.now() + 300 * (attempt + 1) };
          return;
        }
        // Keep a valid displayed frame during a network outage. Missing media
        // and other permanent rendering failures still get actionable errors.
        if (!res.retryable) clearFrame();
        setFrameState("error");
        setFrameError(res.message);
      }
    }).finally(() => {
      if (inFlightRef.current?.controller !== controller) return;
      inFlightRef.current = null;
      pumpRef.current();
    });
  };

  const scheduleFrame = useCallback(
    (t: number) => {
      const pid = projectId;
      if (!pid) {
        cancelQueued();
        clearFrame();
        setFrameState("error");
        setFrameError("尚未选择工程");
        return;
      }
      const request = inFlightRef.current;
      if (request?.pid === pid && Math.abs(request.t - t) < 0.001 && !queuedTargetRef.current) return;
      queuedTargetRef.current = { pid, t, attempt: 0, notBefore: 0 };
      pumpRef.current();
    },
    [projectId, cancelQueued, clearFrame],
  );

  const reset = useCallback(() => {
    cancelQueued();
    lastReq.current = 0;
    clearFrame();
    setFrameState("loading");
    setFrameError(null);
  }, [cancelQueued, clearFrame]);

  useEffect(() => {
    if (!active) cancelQueued();
  }, [active, cancelQueued]);

  // image 模式播放循环（rAF 推进播放头 + 拉帧）
  useEffect(() => {
    if (!active || !playing) return;
    let prevTs = performance.now();
    const tick = (ts: number) => {
      const dt = (ts - prevTs) / 1000;
      prevTs = ts;
      // 图片帧是视频在线播放失败时的功能性降级，而不是慢放模式。
      // 必须按真实时间推进播放头；拉帧节流只影响刷新频率，不应改变时长。
      const next = playheadRef.current + dt;
      if (next >= totalSecs) {
        dispatchRef.current({ type: "PLAYHEAD_SET", t: totalSecs });
        onEnded();
        return;
      }
      dispatchRef.current({ type: "PLAYHEAD_SET", t: next });
      scheduleFrame(next);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [active, playing, totalSecs, scheduleFrame, playheadRef, dispatchRef, onEnded]);

  // 卸载清理
  useEffect(() => {
    return () => {
      cancelQueued();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (frameUrlRef.current) URL.revokeObjectURL(frameUrlRef.current);
      frameUrlRef.current = null;
    };
  }, [cancelQueued]);

  return { frameUrl, frameState, frameError, scheduleFrame, reset };
}
