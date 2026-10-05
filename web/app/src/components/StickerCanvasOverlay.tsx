/** Drag, resize, or rotate the selected sticker in the preview canvas.
 *  The gesture is local until release, so one gesture is one undo step.
 */
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useEditor } from "../store/editor";
import { getLatestState } from "../store/actions";
import { updateEffect } from "../store/effectEdit";
import { rationalToSecs } from "../lib/rational";

type Geometry = { x: number; y: number; size: number; rotation: number };
type Gesture = {
  mode: "move" | "resize" | "rotate";
  pointerId: number;
  startX: number;
  startY: number;
  initial: Geometry;
};

const TRANSFORM = "cutvoke.transform";
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
const roundAngle = (value: number) => Math.round(value * 10) / 10;

export function StickerCanvasOverlay() {
  const { state, dispatch } = useEditor();
  const project = state.project;
  const track = project?.sequence.tracks.find((item) => item.id === state.selection?.trackId);
  const clip = track?.clips.find((item) => item.id === state.selection?.clipId);
  const transform = clip?.effects?.find((effect) => effect.effectId === TRANSFORM && effect.enabled !== false);
  const params = transform?.params || {};
  const paramsKey = JSON.stringify(params);
  const width = project?.sequence.width || 1920;
  const height = project?.sequence.height || 1080;
  const unit = Math.min(width, height);
  const position = params.position as { x?: number; y?: number } | undefined;
  const saved: Geometry = {
    x: Number(position?.x ?? 0),
    y: Number(position?.y ?? 0),
    size: unit * Number(params.scale ?? 0.28),
    rotation: Number(params.rotation ?? 0),
  };
  const svgRef = useRef<SVGSVGElement>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const previewRef = useRef<Geometry | null>(null);
  const [preview, setPreview] = useState<Geometry | null>(null);

  useEffect(() => {
    gestureRef.current = null;
    previewRef.current = null;
    setPreview(null);
  }, [clip?.id, paramsKey, state.editLock]);

  if (!project || !clip || !transform || track?.role !== "sticker" ||
      track.visible === false || clip.hidden ||
      rationalToSecs(clip.timelineStart) > state.playhead ||
      rationalToSecs(clip.timelineEnd) <= state.playhead) return null;

  const editable = !state.editLock && !track.locked;
  const current = preview || saved;
  // Permit deliberate partial placement outside the canvas, while retaining
  // enough of the sticker for a user to grab it again.
  const minVisible = Math.max(8, unit * 0.04);
  const bounded = (next: Geometry): Geometry => ({
    ...next,
    x: clamp(next.x, minVisible - next.size, width - minVisible),
    y: clamp(next.y, minVisible - next.size, height - minVisible),
  });
  const point = (event: PointerEvent<SVGElement>) => {
    const matrix = svgRef.current?.getScreenCTM();
    if (!matrix) return null;
    const local = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return { x: local.x, y: local.y };
  };
  const commit = async (next: Geometry) => {
    if (!editable) return;
    const positionX = Math.round(next.x);
    const positionY = Math.round(next.y);
    const scale = Math.round((next.size / unit) * 10000) / 10000;
    const rotation = roundAngle(next.rotation);
    if (positionX === Math.round(saved.x) && positionY === Math.round(saved.y) &&
        scale === Math.round(Number(params.scale ?? 0.28) * 10000) / 10000 &&
        rotation === roundAngle(Number(params.rotation ?? 0))) return;
    await updateEffect(dispatch, getLatestState() || state, {
      clipId: clip.id,
      effectId: TRANSFORM,
      params: { position: { x: positionX, y: positionY }, scale, rotation },
    });
  };
  const begin = (event: PointerEvent<SVGElement>, mode: Gesture["mode"]) => {
    if (!editable) return;
    const at = point(event);
    if (!at) return;
    event.preventDefault();
    event.stopPropagation();
    gestureRef.current = { mode, pointerId: event.pointerId,
      startX: at.x, startY: at.y, initial: current };
    previewRef.current = current;
    setPreview(current);
    svgRef.current?.setPointerCapture(event.pointerId);
  };
  const move = (event: PointerEvent<SVGSVGElement>) => {
    const gesture = gestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    const at = point(event);
    if (!at) return;
    const dx = at.x - gesture.startX;
    const dy = at.y - gesture.startY;
    let next: Geometry;
    if (gesture.mode === "move") {
      next = bounded({ ...gesture.initial,
        x: gesture.initial.x + dx, y: gesture.initial.y + dy });
    } else if (gesture.mode === "resize") {
      next = bounded({ ...gesture.initial,
        size: clamp(gesture.initial.size + (dx + dy) / 2, unit * 0.04, unit * 2) });
    } else {
      const centerX = gesture.initial.x + gesture.initial.size / 2;
      const centerY = gesture.initial.y + gesture.initial.size / 2;
      const before = Math.atan2(gesture.startY - centerY, gesture.startX - centerX);
      const after = Math.atan2(at.y - centerY, at.x - centerX);
      const delta = Math.atan2(Math.sin(after - before), Math.cos(after - before));
      const rawRotation = gesture.initial.rotation + delta * 180 / Math.PI;
      next = { ...gesture.initial, rotation: event.shiftKey
        ? Math.round(rawRotation / 15) * 15 : roundAngle(rawRotation) };
    }
    previewRef.current = next;
    setPreview(next);
  };
  const finish = (event: PointerEvent<SVGSVGElement>) => {
    const gesture = gestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    // Pointer-up can omit Shift even when the last visible rotation frame was
    // snapped; preserve that frame instead of replacing it with an unsnapped one.
    if (gesture.mode !== "rotate") move(event);
    gestureRef.current = null;
    const next = previewRef.current;
    previewRef.current = null;
    setPreview(null);
    if (next) void commit(next);
  };
  const cancel = () => {
    gestureRef.current = null;
    previewRef.current = null;
    setPreview(null);
  };
  const keyMove = (event: KeyboardEvent<SVGElement>, mode: Gesture["mode"]) => {
    if (!editable) return;
    const step = event.shiftKey ? 10 : 1;
    const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
    const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
    if (!dx && !dy) return;
    if (mode === "rotate" && !dx) return;
    event.preventDefault();
    event.stopPropagation();
    const next = mode === "move"
      ? bounded({ ...current, x: current.x + dx, y: current.y + dy })
      : mode === "resize"
      ? bounded({ ...current, size: clamp(current.size + dx + dy, unit * 0.04, unit * 2) })
      : { ...current, rotation: roundAngle(current.rotation + dx) };
    void commit(next);
  };

  const centerX = current.x + current.size / 2;
  const centerY = current.y + current.size / 2;
  const handleRadius = current.size * 0.4;
  const radians = current.rotation * Math.PI / 180;
  const rotationX = centerX + Math.sin(radians) * handleRadius;
  const rotationY = centerY - Math.cos(radians) * handleRadius;

  return (
    <svg ref={svgRef} className="player__sticker-overlay"
      viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet"
      aria-label="画布贴纸控件" onPointerMove={move} onPointerUp={finish}
      onPointerCancel={cancel}>
      <rect className="player__sticker-outline" x={current.x} y={current.y}
        width={current.size} height={current.size}
        pointerEvents={editable ? "all" : "none"}
        tabIndex={editable ? 0 : -1} role="button" aria-label="移动贴纸"
        onPointerDown={(event) => begin(event, "move")}
        onKeyDown={(event) => keyMove(event, "move")} />
      <circle className="player__sticker-handle"
        cx={current.x + current.size} cy={current.y + current.size}
        r={Math.max(7, unit * 0.012)}
        pointerEvents={editable ? "all" : "none"}
        tabIndex={editable ? 0 : -1} role="button" aria-label="调整贴纸大小"
        onPointerDown={(event) => begin(event, "resize")}
        onKeyDown={(event) => keyMove(event, "resize")} />
      <line className="player__sticker-rotation-guide"
        x1={centerX} y1={centerY} x2={rotationX} y2={rotationY} />
      <circle className="player__sticker-rotation-handle"
        cx={rotationX} cy={rotationY} r={Math.max(7, unit * 0.012)}
        pointerEvents={editable ? "all" : "none"}
        tabIndex={editable ? 0 : -1} role="button" aria-label="旋转贴纸"
        aria-valuetext={`${roundAngle(current.rotation)} 度`}
        onPointerDown={(event) => begin(event, "rotate")}
        onKeyDown={(event) => keyMove(event, "rotate")}>
        <title>拖动旋转；按住 Shift 以 15° 吸附</title>
      </circle>
    </svg>
  );
}
