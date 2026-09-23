/** 预览帧图片轮询降级 Hook：preview-media 不可用时回退 preview-frame 图片。
    节流 300ms；image 模式播放用 rAF 推进播放头。 */

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
  const seqRef = useRef(0);
  const lastReq = useRef(0);
  const queued = useRef(false);
  const queuedTargetRef = useRef<{ pid: string; t: number } | null>(null);
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
    queued.current = false;
    queuedTargetRef.current = null;
  }, []);

  const doFetch = useCallback((pid: string, t: number) => {
    const seq = ++seqRef.current;
    void fetchPreviewFrame({ projectId: pid, t, width: PREVIEW_W, height: PREVIEW_H }).then((res) => {
      // 工程切换或新播放头请求后，旧响应绝不能覆盖新帧；它创建的 URL 也必须释放。
      if (seq !== seqRef.current) {
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
      } else if (res.kind === "empty") {
        clearFrame();
        setFrameState("empty");
      } else {
        clearFrame();
        setFrameState("error");
      }
    });
  }, [clearFrame]);

  const scheduleFrame = useCallback(
    (t: number) => {
      const pid = projectId;
      if (!pid) {
        cancelQueued();
        clearFrame();
        setFrameState("error");
        return;
      }
      // 队列只保留“用户现在想看的位置”。不要在这里淘汰已在路上的请求：
      // 降级播放会以 rAF 高频调度，过早淘汰会让图片模式一直拿不到可显示帧。
      // 真正发起下一次请求时 doFetch 才会使更旧响应失效。
      if (queued.current) {
        queuedTargetRef.current = { pid, t };
        return;
      }
      const now = Date.now();
      if (now - lastReq.current < 300) {
        queued.current = true;
        queuedTargetRef.current = { pid, t };
        timerRef.current = window.setTimeout(() => {
          timerRef.current = null;
          queued.current = false;
          const target = queuedTargetRef.current;
          queuedTargetRef.current = null;
          if (!target) return;
          lastReq.current = Date.now();
          doFetch(target.pid, target.t);
        }, Math.max(0, 300 - (now - lastReq.current)));
        return;
      }
      lastReq.current = Date.now();
      doFetch(pid, t);
    },
    [projectId, doFetch, cancelQueued, clearFrame],
  );

  const reset = useCallback(() => {
    cancelQueued();
    seqRef.current++;
    lastReq.current = 0;
    clearFrame();
    setFrameState("loading");
  }, [cancelQueued, clearFrame]);

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
      seqRef.current++;
      cancelQueued();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (frameUrlRef.current) URL.revokeObjectURL(frameUrlRef.current);
      frameUrlRef.current = null;
    };
  }, [cancelQueued]);

  return { frameUrl, frameState, scheduleFrame, reset };
}
