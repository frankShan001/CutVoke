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
  const captions = project?.sequence.captions || [];
  const projW = project?.sequence.width || 1920;

  const overlayRef = useRef<HTMLDivElement>(null);
  const [frameW, setFrameW] = useState(0);
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const [preview, setPreview] = useState<Geom | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);

  // 跟踪画布显示宽度，用于把字幕字号按显示比例换算（仅编辑手感，非渲染契约）。
  useEffect(() => {
    const el = overlayRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setFrameW(el.clientWidth));
    ro.observe(el);
    setFrameW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const visible = captions.filter(
    (c) => rationalToSecs(c.start) <= state.playhead + 1e-4 && state.playhead < rationalToSecs(c.end) - 1e-4,
  );

  const geomOf = (c: Caption): Geom =>
    gesture && preview && gesture.id === c.id ? preview : norm(c);

  const commit = async (id: string, g: Geom) => {
    await updateCaption(dispatch, state, {
      captionId: id,
      x: g.x,
      y: g.y,
      scale: g.scale,
      rotation: g.rotation,
    });
  };

  const beginMove = (e: ReactPointerEvent, c: Caption) => {
    e.stopPropagation();
    const rect = overlayRef.current?.getBoundingClientRect();
    if (!rect) return;
    const g = norm(c);
    setActiveId(c.id);
    setPreview(g);
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

  const onUp = () => {
    if (!gesture) return;
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
        const baseFont = (c.fontSize || 48) * (frameW / projW || 1);
        return (
          <div
            key={c.id}
            className={`caption-overlay__box${c.id === activeId ? " caption-overlay__box--active" : ""}`}
            style={{
              left: `${g.x * 100}%`,
              top: `${g.y * 100}%`,
              transform: `translate(-50%, -50%) rotate(${g.rotation}deg) scale(${g.scale})`,
              fontSize: `${baseFont}px`,
            }}
            onPointerDown={(e) => beginMove(e, c)}
            onPointerMove={onMove}
            onPointerUp={onUp}
          >
            <span className="caption-overlay__text">{c.text}</span>
            {c.id === activeId ? (
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
