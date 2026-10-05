/** Direct manipulation for a selected clip's normalized crop rectangle. */
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useEditor } from "../store/editor";
import { getLatestState } from "../store/actions";
import { updateEffect } from "../store/effectEdit";
import { rationalToSecs } from "../lib/rational";

type Corner = "nw" | "ne" | "sw" | "se";
type Geometry = { x: number; y: number; w: number; h: number };
type Gesture = {
  mode: "move" | Corner;
  pointerId: number;
  startX: number;
  startY: number;
  initial: Geometry;
};

const CROP = "cutvoke.fx.crop";
const MIN_SIZE = 0.1;
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const rounded = (value: number) => Math.round(value * 10000) / 10000;

function geometry(params: Record<string, unknown>): Geometry {
  const w = clamp(Number(params.w ?? 1), MIN_SIZE, 1);
  const h = clamp(Number(params.h ?? 1), MIN_SIZE, 1);
  return {
    x: clamp(Number(params.x ?? 0), 0, 1 - w),
    y: clamp(Number(params.y ?? 0), 0, 1 - h),
    w,
    h,
  };
}

export function CropCanvasOverlay() {
  const { state, dispatch } = useEditor();
  const project = state.project;
  const track = project?.sequence.tracks.find((item) => item.id === state.selection?.trackId);
  const clip = track?.clips.find((item) => item.id === state.selection?.clipId);
  const effect = clip?.effects?.find((item) => item.effectId === CROP && item.enabled !== false);
  const params = (effect?.params || {}) as Record<string, unknown>;
  const paramsKey = JSON.stringify(params);
  const width = project?.sequence.width || 1920;
  const height = project?.sequence.height || 1080;
  const svgRef = useRef<SVGSVGElement>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const previewRef = useRef<Geometry | null>(null);
  const [preview, setPreview] = useState<Geometry | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    gestureRef.current = null;
    previewRef.current = null;
    setDragging(false);
    setPreview(null);
  }, [clip?.id, paramsKey, state.editLock]);

  if (!project || !clip || !effect || track?.kind !== "video" ||
      track.visible === false || clip.hidden ||
      rationalToSecs(clip.timelineStart) > state.playhead ||
      rationalToSecs(clip.timelineEnd) <= state.playhead) return null;

  const current = preview || geometry(params);
  const editable = !state.editLock && !track.locked;
  const point = (event: PointerEvent<SVGElement>) => {
    const matrix = svgRef.current?.getScreenCTM();
    if (!matrix) return null;
    const local = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return { x: local.x / width, y: local.y / height };
  };
  const commit = async (next: Geometry) => {
    if (!editable) return;
    const before = geometry(params);
    if ((["x", "y", "w", "h"] as const).every((key) =>
      rounded(next[key]) === rounded(before[key]))) return;
    await updateEffect(dispatch, getLatestState() || state, {
      clipId: clip.id,
      effectId: CROP,
      params: { x: rounded(next.x), y: rounded(next.y),
                w: rounded(next.w), h: rounded(next.h) },
    });
  };
  const begin = (event: PointerEvent<SVGElement>, mode: Gesture["mode"]) => {
    if (!editable) return;
    event.preventDefault();
    event.stopPropagation();
    const at = point(event);
    if (!at) return;
    gestureRef.current = { mode, pointerId: event.pointerId,
      startX: at.x, startY: at.y, initial: current };
    previewRef.current = current;
    setPreview(current);
    setDragging(true);
    svgRef.current?.setPointerCapture(event.pointerId);
  };
  const move = (event: PointerEvent<SVGSVGElement>) => {
    const gesture = gestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    const at = point(event);
    if (!at) return;
    const start = gesture.initial;
    const dx = at.x - gesture.startX;
    const dy = at.y - gesture.startY;
    let next: Geometry;
    if (gesture.mode === "move") {
      next = { ...start,
        x: rounded(clamp(start.x + dx, 0, 1 - start.w)),
        y: rounded(clamp(start.y + dy, 0, 1 - start.h)),
      };
    } else {
      let left = start.x;
      let top = start.y;
      let right = start.x + start.w;
      let bottom = start.y + start.h;
      if (gesture.mode.includes("w")) left = clamp(start.x + dx, 0, right - MIN_SIZE);
      else right = clamp(start.x + start.w + dx, left + MIN_SIZE, 1);
      if (gesture.mode.includes("n")) top = clamp(start.y + dy, 0, bottom - MIN_SIZE);
      else bottom = clamp(start.y + start.h + dy, top + MIN_SIZE, 1);
      next = { x: rounded(left), y: rounded(top),
        w: rounded(right - left), h: rounded(bottom - top) };
    }
    previewRef.current = next;
    setPreview(next);
  };
  const finish = (event: PointerEvent<SVGSVGElement>) => {
    if (gestureRef.current?.pointerId !== event.pointerId) return;
    move(event);
    gestureRef.current = null;
    setDragging(false);
    const next = previewRef.current;
    previewRef.current = null;
    setPreview(null);
    if (next) void commit(next);
  };
  const cancel = () => {
    gestureRef.current = null;
    previewRef.current = null;
    setDragging(false);
    setPreview(null);
  };
  const keyboardAdjust = (event: KeyboardEvent<SVGElement>, mode: Gesture["mode"]) => {
    if (!editable) return;
    const delta = event.shiftKey ? 0.05 : 0.01;
    const dx = event.key === "ArrowLeft" ? -delta : event.key === "ArrowRight" ? delta : 0;
    const dy = event.key === "ArrowUp" ? -delta : event.key === "ArrowDown" ? delta : 0;
    if (!dx && !dy) return;
    event.preventDefault();
    event.stopPropagation();
    const initial = current;
    let next: Geometry;
    if (mode === "move") {
      next = { ...initial,
        x: clamp(initial.x + dx, 0, 1 - initial.w),
        y: clamp(initial.y + dy, 0, 1 - initial.h),
      };
    } else {
      let left = initial.x;
      let top = initial.y;
      let right = initial.x + initial.w;
      let bottom = initial.y + initial.h;
      if (mode.includes("w")) left = clamp(initial.x + dx, 0, right - MIN_SIZE);
      else right = clamp(right + dx, left + MIN_SIZE, 1);
      if (mode.includes("n")) top = clamp(initial.y + dy, 0, bottom - MIN_SIZE);
      else bottom = clamp(bottom + dy, top + MIN_SIZE, 1);
      next = { x: left, y: top, w: right - left, h: bottom - top };
    }
    void commit(next);
  };

  const x = current.x * width;
  const y = current.y * height;
  const w = current.w * width;
  const h = current.h * height;
  const radius = Math.max(6, Math.min(width, height) * 0.012);
  const handles: { corner: Corner; cx: number; cy: number; cursor: string; label: string }[] = [
    { corner: "nw", cx: clamp(x, 1, width - 1), cy: clamp(y, 1, height - 1), cursor: "nwse-resize", label: "左上角" },
    { corner: "ne", cx: clamp(x + w, 1, width - 1), cy: clamp(y, 1, height - 1), cursor: "nesw-resize", label: "右上角" },
    { corner: "sw", cx: clamp(x, 1, width - 1), cy: clamp(y + h, 1, height - 1), cursor: "nesw-resize", label: "左下角" },
    { corner: "se", cx: clamp(x + w, 1, width - 1), cy: clamp(y + h, 1, height - 1), cursor: "nwse-resize", label: "右下角" },
  ];

  return (
    <svg ref={svgRef} className="player__crop-overlay"
      viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet"
      aria-label="画布裁切控件" onPointerMove={move} onPointerUp={finish}
      onPointerCancel={cancel}>
      <rect className="player__crop-shade" x="0" y="0" width={width} height={y} />
      <rect className="player__crop-shade" x="0" y={y + h} width={width} height={height - y - h} />
      <rect className="player__crop-shade" x="0" y={y} width={x} height={h} />
      <rect className="player__crop-shade" x={x + w} y={y} width={width - x - w} height={h} />
      <rect className="player__crop-outline" x={x} y={y} width={w} height={h}
        tabIndex={editable ? 0 : -1} role="button" aria-label="移动裁切区域"
        onPointerDown={(event) => begin(event, "move")}
        onKeyDown={(event) => keyboardAdjust(event, "move")} />
      <path className="player__crop-grid"
        d={`M ${x + w / 3} ${y} V ${y + h} M ${x + 2 * w / 3} ${y} V ${y + h} ` +
           `M ${x} ${y + h / 3} H ${x + w} M ${x} ${y + 2 * h / 3} H ${x + w}`} />
      {handles.map((handle) => (
        <circle key={handle.corner} className="player__crop-handle"
          cx={handle.cx} cy={handle.cy} r={radius} cursor={handle.cursor}
          tabIndex={editable ? 0 : -1} role="button"
          aria-label={`调整裁切${handle.label}`}
          onPointerDown={(event) => begin(event, handle.corner)}
          onKeyDown={(event) => keyboardAdjust(event, handle.corner)} />
      ))}
      {dragging ? <text className="player__crop-size" x={x} y={Math.max(18, y - 8)}>
        {Math.round(current.w * 100)}% × {Math.round(current.h * 100)}%
      </text> : null}
    </svg>
  );
}
