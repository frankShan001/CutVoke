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
  const rafRef = useRef<number | null>(null);

  const doFetch = useCallback((pid: string, t: number) => {
    const seq = ++seqRef.current;
    void fetchPreviewFrame({ projectId: pid, t, width: PREVIEW_W, height: PREVIEW_H }).then((res) => {
      if (seq !== seqRef.current) return;
      if (res.kind === "frame") {
        setFrameUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return res.url;
        });
        setFrameState("ok");
      } else if (res.kind === "empty") {
        setFrameUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return null;
        });
        setFrameState("empty");
      } else {
        setFrameUrl(null);
        setFrameState("error");
      }
    });
  }, []);

  const scheduleFrame = useCallback(
    (t: number) => {
      const pid = projectId;
      if (!pid) {
        setFrameUrl(null);
        setFrameState("error");
        return;
      }
      if (queued.current) return;
      const now = Date.now();
      if (now - lastReq.current < 300) {
        queued.current = true;
        window.setTimeout(() => {
          queued.current = false;
          doFetch(pid, t);
        }, 320 - (now - lastReq.current));
        return;
      }
      lastReq.current = Date.now();
      doFetch(pid, t);
    },
    [projectId, doFetch],
  );

  const reset = useCallback(() => {
    seqRef.current++;
    setFrameUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setFrameState("loading");
  }, []);

  // image 模式播放循环（rAF 推进播放头 + 拉帧）
  useEffect(() => {
    if (!active || !playing) return;
    let prevTs = performance.now();
    const tick = (ts: number) => {
      const dt = (ts - prevTs) / 1000;
      prevTs = ts;
      const next = playheadRef.current + dt * 0.5;
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
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return { frameUrl, frameState, scheduleFrame, reset };
}
