/** 画布字幕叠层（H01）：在预览画面（player__canvas-frame 内）上渲染当前播放头可见的字幕，
    支持直接拖拽改 x/y、角柄缩放、顶柄旋转。拖动中只做本地预览，松手才提交 caption.update。
    归一化坐标换算：normalized = 像素 / 画布显示尺寸（画布随缩放/比例缩放，故用叠层自身 rect）。
    不前端夹紧越界值：越界（x/y 超 0~1、scale 超 0.1~5、rotation 超 ±180）由后端整体拒绝，
    runCommand 会把后端错误反馈到状态栏与错误浮窗，字幕回退到上次合法位置。 */

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useEditor } from "../store/editor";
import { updateCaption } from "../store/clipEdit";
import { rationalToSecs } from "../lib/rational";
import type { Caption } from "../types/api";

type Geom = { x: number; y: number; scale: number; rotation: number };

type Gesture =
  | { mode: "move"; id: string; startCX: number; startCY: number; startNX: number; startNY: number; rectW: number; rectH: number }
  | { mode: "scale"; id: string; cx: number; cy: number; startDist: number; startScale: number }
  | { mode: "rotate"; id: string; cx: number; cy: number; startAngle: number; startRot: number };

const norm = (c: Caption): Geom => ({
  x: c.x ?? 0.5,
  y: c.y ?? 0.5,
  scale: c.scale ?? 1,
  rotation: c.rotation ?? 0,
});

export function CaptionCanvasOverlay() {
  const { state, dispatch } = useEditor();
  const project = state.project;
  const draft = !state.editLock && state.captionDraftPreview?.projectId === project?.projectId
    ? state.captionDraftPreview
    : null;
  const captions = (project?.sequence.captions || []).map((caption) => (
    draft?.captionId === caption.id
      ? { ...caption, text: draft.text, start: draft.start, end: draft.end }
      : caption
  ));
  const projW = project?.sequence.width || 1920;
  const projH = project?.sequence.height || 1080;

  const overlayRef = useRef<HTMLDivElement>(null);
  const moveStartRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 });
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const [preview, setPreview] = useState<Geom | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);

  // Agent 在拖拽过程中取得租约时，立即丢弃尚未提交的本地预览；否则用户松开鼠标
  // 后可能试图把锁前的位置写回。后端仍是最终的权限边界。
  useEffect(() => {
    if (!state.editLock) return;
    moveStartRef.current = null;
    setGesture(null);
    setPreview(null);
  }, [state.editLock]);

  // 跟踪画布显示尺寸；字号沿用导出的 1080p 基准，描边/阴影按工程画布缩放。
  useEffect(() => {
    const el = overlayRef.current;
    if (!el) return;
    const updateFrameSize = () => setFrameSize({ width: el.clientWidth, height: el.clientHeight });
    const ro = new ResizeObserver(updateFrameSize);
    ro.observe(el);
    updateFrameSize();
    return () => ro.disconnect();
  }, []);

  const visible = captions.filter(
    (c) => rationalToSecs(c.start) <= state.playhead + 1e-4 && state.playhead < rationalToSecs(c.end) - 1e-4,
  );

  const geomOf = (c: Caption): Geom =>
    gesture && preview && gesture.id === c.id ? preview : norm(c);

  const opacityAt = (c: Caption) => {
    const now = state.playhead;
    const start = rationalToSecs(c.start);
    const end = rationalToSecs(c.end);
    const fadeIn = c.animIn ? Math.min(1, Math.max(0, ((now - start) * 1000) / c.animIn)) : 1;
    const fadeOut = c.animOut ? Math.min(1, Math.max(0, ((end - now) * 1000) / c.animOut)) : 1;
    return Math.min(fadeIn, fadeOut);
  };

  const commit = async (id: string, g: Geom) => {
    const caption = captions.find((item) => item.id === id);
    if (!caption) return;
    const current = norm(caption);
    const update: Parameters<typeof updateCaption>[2] = { captionId: id };
    if (g.x !== current.x) update.x = g.x;
    if (g.y !== current.y) update.y = g.y;
    if (g.scale !== current.scale) update.scale = g.scale;
    if (g.rotation !== current.rotation) update.rotation = g.rotation;
    if (Object.keys(update).length > 1) await updateCaption(dispatch, state, update);
  };

  const beginMove = (e: ReactPointerEvent, c: Caption) => {
    if (state.editLock) return;
    e.stopPropagation();
    const rect = overlayRef.current?.getBoundingClientRect();
    if (!rect) return;
    const g = norm(c);
    setActiveId(c.id);
    setPreview(g);
    moveStartRef.current = { pointerId: e.pointerId, x: e.clientX, y: e.clientY };
    setGesture({
      mode: "move",
      id: c.id,
      startCX: e.clientX,
      startCY: e.clientY,
      startNX: g.x,
      startNY: g.y,
      rectW: rect.width,
      rectH: rect.height,
    });
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };

  const beginScale = (e: ReactPointerEvent, c: Caption) => {
    if (state.editLock) return;
    e.stopPropagation();
    const rect = overlayRef.current?.getBoundingClientRect();
    if (!rect) return;
    const g = norm(c);
    const cx = rect.left + g.x * rect.width;
    const cy = rect.top + g.y * rect.height;
    setActiveId(c.id);
    setPreview(g);
    setGesture({ mode: "scale", id: c.id, cx, cy, startDist: Math.max(8, Math.hypot(e.clientX - cx, e.clientY - cy)), startScale: g.scale });
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };

  const beginRotate = (e: ReactPointerEvent, c: Caption) => {
    if (state.editLock) return;
    e.stopPropagation();
    const rect = overlayRef.current?.getBoundingClientRect();
    if (!rect) return;
    const g = norm(c);
    const cx = rect.left + g.x * rect.width;
    const cy = rect.top + g.y * rect.height;
    setActiveId(c.id);
    setPreview(g);
    const startAngle = (Math.atan2(e.clientY - cy, e.clientX - cx) * 180) / Math.PI;
    setGesture({ mode: "rotate", id: c.id, cx, cy, startAngle, startRot: g.rotation });
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };

  const onMove = (e: ReactPointerEvent) => {
    if (!gesture) return;
    if (gesture.mode === "move") {
      const start = moveStartRef.current;
      if (start?.pointerId === e.pointerId) {
        if (Math.hypot(e.clientX - start.x, e.clientY - start.y) < 6) return;
        moveStartRef.current = null;
      }
      const dx = (e.clientX - gesture.startCX) / gesture.rectW;
      const dy = (e.clientY - gesture.startCY) / gesture.rectH;
      setPreview((p) => (p ? { ...p, x: gesture.startNX + dx, y: gesture.startNY + dy } : p));
    } else if (gesture.mode === "scale") {
      const dist = Math.hypot(e.clientX - gesture.cx, e.clientY - gesture.cy);
      const scale = gesture.startScale * (dist / gesture.startDist);
      setPreview((p) => (p ? { ...p, scale } : p));
    } else {
      const ang = (Math.atan2(e.clientY - gesture.cy, e.clientX - gesture.cx) * 180) / Math.PI;
      const rotation = gesture.startRot + (ang - gesture.startAngle);
      setPreview((p) => (p ? { ...p, rotation } : p));
    }
  };

  const onUp = (e: ReactPointerEvent) => {
    if (!gesture) return;
    if (moveStartRef.current?.pointerId === e.pointerId) moveStartRef.current = null;
    const id = gesture.id;
    const g = preview;
    setGesture(null);
    setPreview(null);
    if (g) void commit(id, g);
  };

  if (!state.currentId) return null;

  return (
    <div className="caption-overlay" ref={overlayRef}>
      {visible.map((c) => {
        const g = geomOf(c);
        const canvasScale = frameSize.height / projH || frameSize.width / projW || 1;
        // ASS 先按 sequence 高度把字号从 1080p 基准缩放并取整，再随画布显示比例缩放。
        const renderFontSize = Math.max(8, Math.round((c.fontSize ?? 32) * projH / 1080));
        const baseFont = renderFontSize * canvasScale;
        const stroke = Math.max(0, c.strokeWidth ?? 2) * canvasScale;
        const shadow = Math.max(0, c.shadow ?? 1) * canvasScale;
        const align = c.align === "left" || c.align === "right" ? c.align : "center";
        const anchorX = align === "left" ? "0" : align === "right" ? "-100" : "-50";
        return (
          <div
            key={c.id}
            className={`caption-overlay__box${c.id === activeId ? " caption-overlay__box--active" : ""}`}
            style={{
              left: `${g.x * 100}%`,
              top: `${g.y * 100}%`,
              transform: `translate(${anchorX}%, -100%) rotate(${g.rotation}deg) scale(${g.scale})`,
              transformOrigin: `${align} bottom`,
              fontSize: `${baseFont}px`,
              textAlign: align,
              justifyContent: align === "left" ? "flex-start" : align === "right" ? "flex-end" : "center",
              backgroundColor: c.background || "transparent",
              opacity: opacityAt(c),
            }}
            onPointerDown={state.editLock ? undefined : (e) => beginMove(e, c)}
            onPointerMove={state.editLock ? undefined : onMove}
            onPointerUp={state.editLock ? undefined : onUp}
          >
            <span
              className="caption-overlay__text"
              style={{
                color: c.color || "#ffffff",
                fontWeight: c.bold ? 700 : 400,
                WebkitTextStroke: stroke ? `${stroke}px ${c.strokeColor || "#000000"}` : undefined,
                // ASS Shadow 是无模糊的右下偏移，颜色取 BackColour；保持预览与导出一致。
                textShadow: shadow ? `${shadow}px ${shadow}px 0 ${c.background || "#000000"}` : "none",
              }}
            >
              {c.text}
            </span>
            {c.id === activeId && !state.editLock ? (
              <>
                <span
                  className="caption-overlay__handle caption-overlay__handle--scale"
                  aria-label="缩放字幕"
                  onPointerDown={(e) => beginScale(e, c)}
                />
                <span
                  className="caption-overlay__handle caption-overlay__handle--rotate"
                  aria-label="旋转字幕"
                  onPointerDown={(e) => beginRotate(e, c)}
                />
              </>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
