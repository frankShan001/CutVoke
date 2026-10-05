/** 普通视频/图片的画布变换框；指针手势只在结束时提交一次命令。 */
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { useEditor } from "../store/editor";
import { getLatestState } from "../store/actions";
import { updateEffect } from "../store/effectEdit";
import { addKeyframes } from "../store/clipEdit";
import { BUILTIN_TRANSFORM_ID } from "../lib/effects";
import { rationalToSecs } from "../lib/rational";
import { evaluateKeyframes } from "../lib/keyframes";

type Mode = "move" | "resize" | "rotate";
type Geometry = { x: number; y: number; scale: number; rotation: number };
type Gesture = { mode: Mode; pointerId: number; startX: number; startY: number; initial: Geometry };
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
const angle = (value: number) => Math.round(value * 10) / 10;

export function TransformCanvasOverlay() {
  const { state, dispatch } = useEditor();
  const project = state.project;
  const track = project?.sequence.tracks.find((item) => item.id === state.selection?.trackId);
  const clip = track?.clips.find((item) => item.id === state.selection?.clipId);
  const effect = clip?.effects?.find((item) => item.effectId === BUILTIN_TRANSFORM_ID && item.enabled !== false);
  const paramsKey = JSON.stringify(effect?.params || {});
  const width = project?.sequence.width || 1920;
  const height = project?.sequence.height || 1080;
  const params = effect?.params || {};
  const position = params.position as { x?: number; y?: number } | undefined;
  const clipStart = clip ? rationalToSecs(clip.timelineStart) : 0;
  const localTime = Math.max(0, state.playhead - clipStart);
  const base: Geometry = {
    x: Number(position?.x ?? 0), y: Number(position?.y ?? 0),
    scale: Number(params.scale ?? 1), rotation: Number(params.rotation ?? 0),
  };
  const saved: Geometry = {
    x: Math.round(evaluateKeyframes(clip?.keyframes?.x, localTime, base.x)),
    y: Math.round(evaluateKeyframes(clip?.keyframes?.y, localTime, base.y)),
    scale: evaluateKeyframes(clip?.keyframes?.scale, localTime, base.scale),
    rotation: evaluateKeyframes(clip?.keyframes?.rotation, localTime, base.rotation),
  };
  const svgRef = useRef<SVGSVGElement>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const previewRef = useRef<Geometry | null>(null);
  const [preview, setPreview] = useState<Geometry | null>(null);

  useEffect(() => {
    gestureRef.current = null;
    previewRef.current = null;
    setPreview(null);
  }, [clip?.id, paramsKey, state.editLock, state.playhead]);

  if (!project || !clip || !track || track.kind !== "video" || track.role === "sticker" ||
      !effect ||
      track.visible === false || clip.hidden ||
      rationalToSecs(clip.timelineStart) > state.playhead || rationalToSecs(clip.timelineEnd) <= state.playhead ||
      clip.effects?.some((item) => item.enabled !== false && /(?:crop|mask)/i.test(item.effectId))) return null;

  const editable = !state.editLock && !track.locked;
  const current = preview || saved;
  const centerX = width / 2 + current.x;
  const centerY = height / 2 + current.y;
  const halfW = width * current.scale / 2;
  const halfH = height * current.scale / 2;
  const radians = current.rotation * Math.PI / 180;
  const rotatePoint = (x: number, y: number) => ({
    x: centerX + x * Math.cos(radians) - y * Math.sin(radians),
    y: centerY + x * Math.sin(radians) + y * Math.cos(radians),
  });
  // Keep the resize target inside the visible canvas at the default 100% scale.
  const resizeHandle = rotatePoint(-halfW, -halfH);
  const rotateHandle = rotatePoint(0, -halfH + Math.min(width, height) * 0.08);
  const point = (event: PointerEvent<SVGElement>) => {
    const matrix = svgRef.current?.getScreenCTM();
    if (!matrix) return null;
    const local = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return { x: local.x, y: local.y };
  };
  const commit = async (next: Geometry) => {
    if (!editable) return;
    const moved = Math.round(next.x) !== Math.round(saved.x) || Math.round(next.y) !== Math.round(saved.y);
    const resized = Math.round(next.scale * 10000) !== Math.round(saved.scale * 10000);
    const rotated = angle(next.rotation) !== angle(saved.rotation);
    if (!moved && !resized && !rotated) return;
    const ctx = getLatestState() || state;
    const frames: { param: string; time: number; value: number; interpolation: "linear" }[] = [];
    const changedBase: Record<string, unknown> = {};
    if (moved && (clip.keyframes?.x?.length || clip.keyframes?.y?.length)) {
      frames.push({ param: "x", time: localTime, value: Math.round(next.x), interpolation: "linear" });
      frames.push({ param: "y", time: localTime, value: Math.round(next.y), interpolation: "linear" });
    } else if (moved) {
      changedBase.position = { x: Math.round(next.x), y: Math.round(next.y) };
    }
    if (resized && clip.keyframes?.scale?.length) {
      frames.push({ param: "scale", time: localTime, value: clamp(next.scale, 0.05, 5), interpolation: "linear" });
    } else if (resized) {
      changedBase.scale = clamp(next.scale, 0.05, 5);
    }
    if (rotated && clip.keyframes?.rotation?.length) {
      frames.push({ param: "rotation", time: localTime, value: clamp(angle(next.rotation), -180, 180), interpolation: "linear" });
    } else if (rotated) {
      changedBase.rotation = clamp(angle(next.rotation), -180, 180);
    }
    if (Object.keys(changedBase).length) {
      await updateEffect(dispatch, ctx, { clipId: clip.id, effectId: BUILTIN_TRANSFORM_ID, params: changedBase });
    }
    if (frames.length) {
      await addKeyframes(dispatch, getLatestState() || ctx, { clipId: clip.id, keyframes: frames });
    }
  };
  const begin = (event: PointerEvent<SVGElement>, mode: Mode) => {
    if (!editable) return;
    const at = point(event);
    if (!at) return;
    event.preventDefault(); event.stopPropagation();
    gestureRef.current = { mode, pointerId: event.pointerId, startX: at.x, startY: at.y, initial: current };
    previewRef.current = current; setPreview(current);
    svgRef.current?.setPointerCapture(event.pointerId);
  };
  const move = (event: PointerEvent<SVGSVGElement>) => {
    const gesture = gestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    const at = point(event);
    if (!at) return;
    const dx = at.x - gesture.startX, dy = at.y - gesture.startY;
    let next: Geometry;
    if (gesture.mode === "move") {
      next = { ...gesture.initial, x: clamp(gesture.initial.x + dx, -width * 3, width * 3), y: clamp(gesture.initial.y + dy, -height * 3, height * 3) };
    } else if (gesture.mode === "resize") {
      const cx = width / 2 + gesture.initial.x, cy = height / 2 + gesture.initial.y;
      const before = Math.hypot(gesture.startX - cx, gesture.startY - cy);
      const after = Math.hypot(at.x - cx, at.y - cy);
      next = { ...gesture.initial, scale: clamp(gesture.initial.scale * after / Math.max(1, before), 0.05, 5) };
    } else {
      const cx = width / 2 + gesture.initial.x, cy = height / 2 + gesture.initial.y;
      const before = Math.atan2(gesture.startY - cy, gesture.startX - cx);
      const after = Math.atan2(at.y - cy, at.x - cx);
      const delta = Math.atan2(Math.sin(after - before), Math.cos(after - before));
      const raw = gesture.initial.rotation + delta * 180 / Math.PI;
      next = { ...gesture.initial, rotation: event.shiftKey ? Math.round(raw / 15) * 15 : angle(raw) };
    }
    previewRef.current = next; setPreview(next);
  };
  const finish = (event: PointerEvent<SVGSVGElement>) => {
    const gesture = gestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    if (gesture.mode !== "rotate") move(event);
    gestureRef.current = null;
    const next = previewRef.current;
    previewRef.current = null; setPreview(null);
    if (next) void commit(next);
  };
  const cancel = () => { gestureRef.current = null; previewRef.current = null; setPreview(null); };
  const keyMove = (event: React.KeyboardEvent<SVGElement>, mode: Mode) => {
    if (!editable) return;
    const step = event.shiftKey ? 10 : 1;
    const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
    const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
    if (!dx && !dy) return;
    event.preventDefault(); event.stopPropagation();
    const next = mode === "move"
      ? { ...current, x: clamp(current.x + dx, -width * 3, width * 3), y: clamp(current.y + dy, -height * 3, height * 3) }
      : mode === "resize" ? { ...current, scale: clamp(current.scale + (dx + dy) / Math.min(width, height), 0.05, 5) }
      : { ...current, rotation: clamp(angle(current.rotation + dx), -180, 180) };
    void commit(next);
  };

  return (
    <svg ref={svgRef} className="player__transform-overlay" viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid meet" aria-label="画布变换控件" onPointerMove={move}
      onPointerUp={finish} onPointerCancel={cancel}>
      <rect className="player__transform-outline" x={centerX - halfW} y={centerY - halfH}
        width={halfW * 2} height={halfH * 2} transform={`rotate(${current.rotation} ${centerX} ${centerY})`}
        pointerEvents={editable ? "stroke" : "none"} tabIndex={editable ? 0 : -1} role="button" aria-label="移动画面"
        onPointerDown={(event) => begin(event, "move")} onKeyDown={(event) => keyMove(event, "move")} />
      <circle className="player__transform-center-handle" cx={centerX} cy={centerY}
        r={Math.max(6, Math.min(width, height) * 0.018)} pointerEvents={editable ? "all" : "none"}
        tabIndex={editable ? 0 : -1} role="button" aria-label="拖动画面位置"
        onPointerDown={(event) => begin(event, "move")} onKeyDown={(event) => keyMove(event, "move")}>
        <title>拖动调整画面位置</title>
      </circle>
      <circle className="player__transform-handle" cx={resizeHandle.x} cy={resizeHandle.y}
        r={Math.max(7, Math.min(width, height) * 0.012)} pointerEvents={editable ? "all" : "none"}
        tabIndex={editable ? 0 : -1} role="button" aria-label="调整画面大小"
        onPointerDown={(event) => begin(event, "resize")} onKeyDown={(event) => keyMove(event, "resize")} />
      <line className="player__transform-guide" x1={centerX} y1={centerY} x2={rotateHandle.x} y2={rotateHandle.y} />
      <circle className="player__transform-handle player__transform-handle--rotate"
        cx={rotateHandle.x} cy={rotateHandle.y} r={Math.max(7, Math.min(width, height) * 0.012)}
        pointerEvents={editable ? "all" : "none"} tabIndex={editable ? 0 : -1} role="button"
        aria-label="旋转画面" aria-valuetext={`${angle(current.rotation)} 度`}
        onPointerDown={(event) => begin(event, "rotate")} onKeyDown={(event) => keyMove(event, "rotate")}>
        <title>拖动旋转；按住 Shift 以 15° 吸附</title>
      </circle>
    </svg>
  );
}
