/** Direct manipulation for geometry, text and freehand alpha masks. */

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useEditor } from "../store/editor";
import { getLatestState } from "../store/actions";
import { updateEffect } from "../store/effectEdit";
import { rationalToSecs } from "../lib/rational";

type Shape = "rect" | "circle" | "freehand" | "text";
type Point = { x: number; y: number };
type PathPoint = Point & { inHandle?: Point; outHandle?: Point; handlesLinked?: boolean };
type PathMode = "linear" | "smooth" | "bezier";
type Geometry = {
  x: number;
  y: number;
  w: number;
  h: number;
  shape: Shape;
  pathMode: PathMode;
  points: PathPoint[];
  content: string;
  fontSize: number;
};
type Gesture = { mode: "move" | "resize" | "point" | "handle"; pointerId: number;
  startX: number; startY: number; initial: Geometry; pointIndex?: number;
  handleKind?: "inHandle" | "outHandle" };

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
const rounded = (value: number) => Math.round(value * 10000) / 10000;

function boundsOf(points: PathPoint[]) {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, w: Math.max(0.01, Math.max(...xs) - x), h: Math.max(0.01, Math.max(...ys) - y) };
}

function bezierHandles(points: PathPoint[]): PathPoint[] {
  return points.map((point, index) => {
    const previous = points[(index - 1 + points.length) % points.length];
    const following = points[(index + 1) % points.length];
    const tangent = { x: (following.x - previous.x) / 6, y: (following.y - previous.y) / 6 };
    const inHandle = point.inHandle || {
      x: rounded(clamp(point.x - tangent.x, 0, 1)),
      y: rounded(clamp(point.y - tangent.y, 0, 1)),
    };
    const outHandle = point.outHandle || {
      x: rounded(clamp(point.x + tangent.x, 0, 1)),
      y: rounded(clamp(point.y + tangent.y, 0, 1)),
    };
    return { ...point, inHandle, outHandle };
  });
}

function smoothClosedPath(points: Point[]): Point[] {
  if (points.length < 8) return points;
  const steps = Math.max(1, Math.min(4, Math.floor(256 / points.length)));
  const sampled: Point[] = [];
  for (let index = 0; index < points.length; index += 1) {
    const p0 = points[(index - 1 + points.length) % points.length];
    const p1 = points[index];
    const p2 = points[(index + 1) % points.length];
    const p3 = points[(index + 2) % points.length];
    for (let step = 0; step < steps; step += 1) {
      const t = step / steps, t2 = t * t, t3 = t2 * t;
      const coordinate = (axis: "x" | "y") => 0.5 * (
        2 * p1[axis] + (-p0[axis] + p2[axis]) * t +
        (2 * p0[axis] - 5 * p1[axis] + 4 * p2[axis] - p3[axis]) * t2 +
        (-p0[axis] + 3 * p1[axis] - 3 * p2[axis] + p3[axis]) * t3
      );
      sampled.push({ x: clamp(coordinate("x"), 0, 1), y: clamp(coordinate("y"), 0, 1) });
    }
  }
  return sampled;
}

function bezierClosedPath(points: PathPoint[]): Point[] {
  const count = points.length;
  if (count < 3) return points;
  const steps = Math.max(1, Math.min(8, Math.floor(256 / count)));
  const controls = bezierHandles(points);
  const sampled: Point[] = [];
  for (let index = 0; index < count; index += 1) {
    const start = controls[index];
    const end = controls[(index + 1) % count];
    for (let step = 0; step < steps; step += 1) {
      const t = step / steps, inverse = 1 - t;
      const a = inverse ** 3, b = 3 * inverse ** 2 * t;
      const c = 3 * inverse * t ** 2, d = t ** 3;
      sampled.push({
        x: clamp(a * start.x + b * start.outHandle!.x + c * end.inHandle!.x + d * end.x, 0, 1),
        y: clamp(a * start.y + b * start.outHandle!.y + c * end.inHandle!.y + d * end.y, 0, 1),
      });
    }
  }
  return sampled;
}

function translatePathPoint(point: PathPoint, dx: number, dy: number): PathPoint {
  return {
    ...point,
    x: rounded(clamp(point.x + dx, 0, 1)), y: rounded(clamp(point.y + dy, 0, 1)),
    ...(point.inHandle ? { inHandle: {
      x: rounded(clamp(point.inHandle.x + dx, 0, 1)),
      y: rounded(clamp(point.inHandle.y + dy, 0, 1)),
    } } : {}),
    ...(point.outHandle ? { outHandle: {
      x: rounded(clamp(point.outHandle.x + dx, 0, 1)),
      y: rounded(clamp(point.outHandle.y + dy, 0, 1)),
    } } : {}),
  };
}

function textUnits(content: string): number {
  return Math.max(1, ...content.split("\n").map((line) => [...line].reduce(
    (total, character) => total + (character.codePointAt(0)! > 0xff ? 1 : 0.58), 0,
  )));
}

function geometry(params: Record<string, unknown>, width: number, height: number): Geometry {
  const rawShape = String(params.shape || "rect");
  const shape: Shape = rawShape === "circle" || rawShape === "freehand" || rawShape === "text"
    ? rawShape : "rect";
  const x = Number(params.x ?? 0.15), y = Number(params.y ?? 0.15);
  const w = Number(params.w ?? 0.7), h = Number(params.h ?? 0.7);
  const points = Array.isArray(params.points) ? params.points.filter((point): point is PathPoint =>
    !!point && typeof point === "object" && Number.isFinite(Number((point as Point).x)) &&
      Number.isFinite(Number((point as Point).y)),
  ).map((point) => {
    const handle = (value: unknown): Point | undefined => {
      if (!value || typeof value !== "object") return undefined;
      const candidate = value as Point;
      if (!Number.isFinite(Number(candidate.x)) || !Number.isFinite(Number(candidate.y))) return undefined;
      return { x: clamp(Number(candidate.x), 0, 1), y: clamp(Number(candidate.y), 0, 1) };
    };
    return {
      x: clamp(Number(point.x), 0, 1), y: clamp(Number(point.y), 0, 1),
      ...(handle(point.inHandle) ? { inHandle: handle(point.inHandle)! } : {}),
      ...(handle(point.outHandle) ? { outHandle: handle(point.outHandle)! } : {}),
      handlesLinked: point.handlesLinked !== false,
    };
  }) : [];
  if (shape === "freehand" && points.length >= 3) {
    return { ...boundsOf(points), shape,
      pathMode: params.pathMode === "smooth" || params.pathMode === "bezier" ? params.pathMode : "linear",
      points, content: "", fontSize: 0.16 };
  }
  const content = String(params.content ?? "文字");
  const fontSize = clamp(Number(params.fontSize ?? 0.16), 0.02, 0.5);
  if (shape === "text") {
    const lines = Math.max(1, content.split("\n").length);
    return {
      x: clamp(x, 0, 1), y: clamp(y, 0, 1),
      w: clamp(fontSize * textUnits(content) * height / Math.max(width, 1) * 0.78, 0.025, 1 - x),
      h: clamp(fontSize * 1.3 * lines, 0.03, 1 - y),
      shape, pathMode: "linear", points: [], content, fontSize,
    };
  }
  return { x, y, w, h, shape, pathMode: "linear", points, content, fontSize };
}

function adjust(start: Geometry, mode: Gesture["mode"], dx: number, dy: number,
  pointIndex?: number, handleKind?: Gesture["handleKind"]): Geometry {
  if (mode === "point" && start.shape === "freehand" && pointIndex !== undefined) {
    const points = start.points.map((point, index) => index === pointIndex
      ? translatePathPoint(point, dx, dy) : point);
    return { ...start, ...boundsOf(points), points };
  }
  if (mode === "handle" && start.shape === "freehand" && pointIndex !== undefined && handleKind) {
    const points = bezierHandles(start.points).map((point, index) => {
      if (index !== pointIndex) return point;
      const moved = {
        x: rounded(clamp(point[handleKind]!.x + dx, 0, 1)),
        y: rounded(clamp(point[handleKind]!.y + dy, 0, 1)),
      };
      const oppositeKind = handleKind === "inHandle" ? "outHandle" : "inHandle";
      const opposite = {
        x: rounded(clamp(2 * point.x - moved.x, 0, 1)),
        y: rounded(clamp(2 * point.y - moved.y, 0, 1)),
      };
      return {
        ...point,
        [handleKind]: moved,
        [oppositeKind]: point.handlesLinked === false ? point[oppositeKind] : opposite,
      };
    });
    return { ...start, points };
  }
  if (mode === "move") {
    if (start.shape === "freehand" && start.points.length >= 3) {
      const bounds = boundsOf(start.points);
      const mx = clamp(dx, -bounds.x, 1 - bounds.x - bounds.w);
      const my = clamp(dy, -bounds.y, 1 - bounds.y - bounds.h);
      const points = start.points.map((point) => translatePathPoint(point, mx, my));
      return { ...start, ...boundsOf(points), points };
    }
    if (start.shape === "circle") {
      return { ...start,
        x: rounded(clamp(start.x + dx, start.w / 2, 1 - start.w / 2)),
        y: rounded(clamp(start.y + dy, start.h / 2, 1 - start.h / 2)) };
    }
    return { ...start,
      x: rounded(clamp(start.x + dx, 0, 1 - start.w)),
      y: rounded(clamp(start.y + dy, 0, 1 - start.h)) };
  }

  if (start.shape === "text") {
    const fontSize = rounded(clamp(start.fontSize + dy, 0.02, 0.5));
    const scale = fontSize / Math.max(start.fontSize, 0.0001);
    return { ...start, fontSize, w: clamp(start.w * scale, 0.025, 1 - start.x),
      h: clamp(start.h * scale, 0.03, 1 - start.y) };
  }
  if (start.shape === "freehand" && start.points.length >= 3) {
    const bounds = boundsOf(start.points);
    const nextW = clamp(bounds.w + dx, 0.01, 1 - bounds.x);
    const nextH = clamp(bounds.h + dy, 0.01, 1 - bounds.y);
    const sx = nextW / bounds.w, sy = nextH / bounds.h;
    const scaleControl = (control: Point | undefined) => control ? {
      x: rounded(clamp(bounds.x + (control.x - bounds.x) * sx, 0, 1)),
      y: rounded(clamp(bounds.y + (control.y - bounds.y) * sy, 0, 1)),
    } : undefined;
    const scalePoint = (point: PathPoint): PathPoint => ({
      x: rounded(clamp(bounds.x + (point.x - bounds.x) * sx, 0, 1)),
      y: rounded(clamp(bounds.y + (point.y - bounds.y) * sy, 0, 1)),
      ...(point.inHandle ? { inHandle: scaleControl(point.inHandle)! } : {}),
      ...(point.outHandle ? { outHandle: scaleControl(point.outHandle)! } : {}),
    });
    const points = start.points.map(scalePoint);
    return { ...start, ...boundsOf(points), points };
  }

  const factor = start.shape === "circle" ? 2 : 1;
  return { ...start,
    w: rounded(clamp(start.w + dx * factor, 0.01,
      Math.max(0.01, start.shape === "circle" ? 2 * Math.min(start.x, 1 - start.x) : 1 - start.x))),
    h: rounded(clamp(start.h + dy * factor, 0.01,
      Math.max(0.01, start.shape === "circle" ? 2 * Math.min(start.y, 1 - start.y) : 1 - start.y))) };
}

export function MaskCanvasOverlay() {
  const { state, dispatch } = useEditor();
  const project = state.project;
  const track = project?.sequence.tracks.find((item) => item.id === state.selection?.trackId);
  const clip = track?.clips.find((item) => item.id === state.selection?.clipId);
  const mask = clip?.effects?.find((effect) => effect.effectId === "cutvoke.fx.mask" && effect.enabled !== false);
  const params = mask?.params || {};
  const paramsKey = JSON.stringify(params);
  const svgRef = useRef<SVGSVGElement>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const previewRef = useRef<Geometry | null>(null);
  const drawPointerId = useRef<number | null>(null);
  const draftRef = useRef<Point[]>([]);
  const [preview, setPreview] = useState<Geometry | null>(null);
  const [manualDraw, setManualDraw] = useState(false);
  const [editAnchors, setEditAnchors] = useState(false);
  const [selectedAnchor, setSelectedAnchor] = useState<number | null>(null);
  const [draftPoints, setDraftPoints] = useState<Point[]>([]);
  const [dragging, setDragging] = useState(false);
  const width = project?.sequence.width || 1920;
  const height = project?.sequence.height || 1080;

  useEffect(() => {
    gestureRef.current = null;
    previewRef.current = null;
    drawPointerId.current = null;
    draftRef.current = [];
    setDragging(false);
    setPreview(null);
    setDraftPoints([]);
    setManualDraw(false);
  }, [clip?.id, paramsKey, state.editLock]);

  useEffect(() => {
    setSelectedAnchor(null);
  }, [clip?.id, params.shape, params.pathMode]);

  if (!project || !clip || !mask || track?.kind !== "video" ||
      rationalToSecs(clip.timelineStart) > state.playhead ||
      rationalToSecs(clip.timelineEnd) <= state.playhead) return null;

  const current = preview || geometry(params, width, height);
  const editable = !state.editLock && !track.locked;
  const drawing = current.shape === "freehand" && (manualDraw || current.points.length < 3);

  const point = (event: PointerEvent<SVGElement>) => {
    const matrix = svgRef.current?.getScreenCTM();
    if (!matrix) return null;
    const local = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return { x: clamp(local.x / width, 0, 1), y: clamp(local.y / height, 0, 1) };
  };

  const commit = async (next: Geometry) => {
    if (!editable) return;
    const before = geometry(params, width, height);
    const geometryChanged = (["x", "y"] as const).some((key) => rounded(next[key]) !== rounded(before[key])) ||
      (next.shape !== "text" && (["w", "h"] as const).some((key) => rounded(next[key]) !== rounded(before[key]))) ||
      (next.shape === "text" && rounded(next.fontSize) !== rounded(before.fontSize));
    const pointsChanged = next.shape === "freehand" && JSON.stringify(next.points) !== JSON.stringify(before.points);
    const pathModeChanged = next.shape === "freehand" && next.pathMode !== before.pathMode;
    if (!geometryChanged && !pointsChanged && !pathModeChanged) return;
    const nextParams: Record<string, unknown> = {
      x: rounded(next.x), y: rounded(next.y), w: rounded(next.w), h: rounded(next.h),
    };
    if (next.shape === "text") nextParams.fontSize = rounded(next.fontSize);
    if (next.shape === "freehand") {
      nextParams.points = next.points.slice(0, 128).map((item) => ({
        x: rounded(item.x), y: rounded(item.y),
        ...(item.inHandle ? { inHandle: { x: rounded(item.inHandle.x), y: rounded(item.inHandle.y) } } : {}),
        ...(item.outHandle ? { outHandle: { x: rounded(item.outHandle.x), y: rounded(item.outHandle.y) } } : {}),
        handlesLinked: item.handlesLinked !== false,
      }));
      nextParams.pathMode = next.pathMode;
    }
    await updateEffect(dispatch, getLatestState() || state, {
      clipId: clip.id, effectId: "cutvoke.fx.mask", params: nextParams,
    });
  };

  const begin = (event: PointerEvent<SVGElement>, mode: Gesture["mode"], pointIndex?: number,
    handleKind?: Gesture["handleKind"]) => {
    if (!editable || drawing) return;
    event.preventDefault();
    event.stopPropagation();
    const at = point(event);
    if (!at) return;
    gestureRef.current = { mode, pointerId: event.pointerId,
      startX: at.x, startY: at.y, initial: current, pointIndex, handleKind };
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
    const next = adjust(gesture.initial, gesture.mode,
      at.x - gesture.startX, at.y - gesture.startY, gesture.pointIndex, gesture.handleKind);
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

  const appendDrawPoint = (event: PointerEvent<SVGSVGElement>) => {
    const at = point(event);
    if (!at) return;
    const points = draftRef.current;
    const last = points[points.length - 1];
    if (last && Math.hypot(at.x - last.x, at.y - last.y) < 0.006) return;
    let next = [...points, { x: rounded(at.x), y: rounded(at.y) }];
    if (next.length > 128) next = next.filter((_item, index) => index % 2 === 0).concat(next[next.length - 1]);
    draftRef.current = next;
    setDraftPoints(next);
  };

  const beginDraw = (event: PointerEvent<SVGSVGElement>) => {
    if (!drawing || !editable) return;
    event.preventDefault();
    event.stopPropagation();
    draftRef.current = [];
    setDraftPoints([]);
    drawPointerId.current = event.pointerId;
    appendDrawPoint(event);
    svgRef.current?.setPointerCapture(event.pointerId);
  };

  const moveDraw = (event: PointerEvent<SVGSVGElement>) => {
    if (drawPointerId.current !== event.pointerId) return;
    appendDrawPoint(event);
  };

  const finishDraw = (event: PointerEvent<SVGSVGElement>) => {
    if (drawPointerId.current !== event.pointerId) return;
    appendDrawPoint(event);
    const points = draftRef.current;
    drawPointerId.current = null;
    draftRef.current = [];
    setDraftPoints([]);
    if (points.length < 3) return;
    const bounds = boundsOf(points);
    const pathMode: PathMode = params.pathMode === "linear" || params.pathMode === "bezier"
      ? params.pathMode : "smooth";
    void commit({ ...current, ...bounds, points, pathMode });
    setManualDraw(false);
  };

  const cancelGesture = () => {
    gestureRef.current = null;
    previewRef.current = null;
    drawPointerId.current = null;
    draftRef.current = [];
    setDragging(false);
    setPreview(null);
    setDraftPoints([]);
  };

  const keyMove = (event: KeyboardEvent<SVGElement>, mode: Gesture["mode"], pointIndex?: number,
    handleKind?: Gesture["handleKind"]) => {
    if (!editable || drawing) return;
    const delta = event.shiftKey ? 0.05 : 0.01;
    const dx = event.key === "ArrowLeft" ? -delta : event.key === "ArrowRight" ? delta : 0;
    const dy = event.key === "ArrowUp" ? -delta : event.key === "ArrowDown" ? delta : 0;
    if (!dx && !dy) return;
    event.preventDefault();
    event.stopPropagation();
    void commit(adjust(current, mode, dx, dy, pointIndex, handleKind));
  };

  const box = current.shape === "circle"
    ? { x: current.x - current.w / 2, y: current.y - current.h / 2, w: current.w, h: current.h }
    : current.shape === "freehand" && current.points.length >= 3
    ? boundsOf(current.points)
    : { x: current.x, y: current.y, w: current.w, h: current.h };
  const handleX = clamp((box.x + box.w) * width, 1, width - 1);
  const handleY = clamp((box.y + box.h) * height, 1, height - 1);
  const handleRadius = Math.max(6, Math.min(width, height) * 0.012);
  const draftSvgPoints = draftPoints.map((item) => `${item.x * width},${item.y * height}`).join(" ");

  return (
    <>
      {current.shape === "freehand" ? (
        <div className="player__mask-tools">
          <button type="button" className="player__mask-tool" disabled={!editable || drawing}
            aria-pressed={current.pathMode !== "linear"}
            title="依次切换折线、平滑和贝塞尔模式"
            onClick={() => {
              const pathMode: PathMode = current.pathMode === "linear" ? "smooth"
                : current.pathMode === "smooth" ? "bezier" : "linear";
              void commit({ ...current, pathMode,
                points: pathMode === "bezier" ? bezierHandles(current.points) : current.points });
              setSelectedAnchor(null);
            }}>
            {current.pathMode === "linear" ? "路径：折线"
              : current.pathMode === "smooth" ? "路径：平滑" : "路径：贝塞尔"}
          </button>
          <button type="button" className="player__mask-tool"
            disabled={!editable || drawing || current.points.length < 3}
            aria-pressed={editAnchors}
            onClick={() => { setManualDraw(false); setSelectedAnchor(null);
              setEditAnchors((value) => !value); }}>
            {editAnchors ? "完成编辑" : "编辑路径点"}
          </button>
          <button type="button" className="player__mask-tool" disabled={!editable}
            onClick={() => { setEditAnchors(false); setManualDraw((value) => !value);
              setSelectedAnchor(null);
              setDraftPoints([]); draftRef.current = []; }}>
            {drawing ? "取消绘制" : "重画钢笔蒙版"}
          </button>
          {drawing ? <span role="status">在预览画布按住拖动，绘制闭合蒙版</span> : null}
          {!drawing && editAnchors && current.pathMode === "bezier" &&
            selectedAnchor !== null && current.points[selectedAnchor] ? (
              <button type="button" className="player__mask-tool" disabled={!editable}
                aria-label={`贝塞尔切线 ${selectedAnchor + 1}：${
                  current.points[selectedAnchor].handlesLinked === false ? "独立" : "联动"}`}
                aria-pressed={current.points[selectedAnchor].handlesLinked === false}
                onClick={() => {
                  const points = bezierHandles(current.points).map((item, index) => index === selectedAnchor
                    ? { ...item, handlesLinked: item.handlesLinked !== false ? false : true }
                    : item);
                  void commit({ ...current, points });
                }}>
                {current.points[selectedAnchor].handlesLinked === false ? "切线：独立" : "切线：联动"}
              </button>
            ) : null}
        </div>
      ) : null}
      <svg ref={svgRef}
        className={`player__mask-overlay${drawing ? " player__mask-overlay--drawing" : ""}`}
        viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet"
        aria-label="画布蒙版控件"
        onPointerDown={beginDraw}
        onPointerMove={drawing ? moveDraw : move}
        onPointerUp={drawing ? finishDraw : finish}
        onPointerCancel={cancelGesture}>
        {drawing ? (
          draftPoints.length > 1 ? <polyline className="player__mask-draft" points={draftSvgPoints} /> : null
        ) : current.shape === "circle" ? (
          <ellipse className="player__mask-outline" cx={current.x * width} cy={current.y * height}
            rx={current.w * width / 2} ry={current.h * height / 2}
            tabIndex={editable ? 0 : -1} role="button" aria-label="移动椭圆蒙版"
            onPointerDown={(event) => begin(event, "move")}
            onKeyDown={(event) => keyMove(event, "move")} />
        ) : current.shape === "freehand" && current.points.length >= 3 ? (
          <polygon className="player__mask-outline"
            points={(current.pathMode === "smooth" ? smoothClosedPath(current.points)
              : current.pathMode === "bezier" ? bezierClosedPath(current.points) : current.points)
              .map((item) => `${item.x * width},${item.y * height}`).join(" ")}
            tabIndex={editable ? 0 : -1} role="button" aria-label="移动钢笔蒙版"
            onPointerDown={(event) => begin(event, "move")}
            onKeyDown={(event) => keyMove(event, "move")} />
        ) : current.shape === "text" ? (
          <>
            <rect className="player__mask-outline" x={box.x * width} y={box.y * height}
              width={box.w * width} height={box.h * height}
              tabIndex={editable ? 0 : -1} role="button" aria-label="移动文字蒙版"
              onPointerDown={(event) => begin(event, "move")}
              onKeyDown={(event) => keyMove(event, "move")} />
            <text className="player__mask-text" x={current.x * width} y={current.y * height}
              fontSize={current.fontSize * height} aria-hidden="true">
              {current.content.split("\n").map((line, index) => (
                <tspan key={`${index}-${line}`} x={current.x * width}
                  dy={index === 0 ? current.fontSize * height : current.fontSize * height * 1.25}>
                  {line}
                </tspan>
              ))}
            </text>
          </>
        ) : (
          <rect className="player__mask-outline" x={box.x * width} y={box.y * height}
            width={box.w * width} height={box.h * height}
            tabIndex={editable ? 0 : -1} role="button" aria-label="移动矩形蒙版"
            onPointerDown={(event) => begin(event, "move")}
            onKeyDown={(event) => keyMove(event, "move")} />
        )}
        {!drawing ? (
          <circle className="player__mask-handle" cx={handleX} cy={handleY} r={handleRadius}
            tabIndex={editable ? 0 : -1} role="button"
            aria-label={current.shape === "text" ? "调整文字蒙版字号" : "调整蒙版大小"}
            onPointerDown={(event) => begin(event, "resize")}
            onKeyDown={(event) => keyMove(event, "resize")} />
        ) : null}
        {!drawing && editAnchors && current.shape === "freehand" ? current.points.map((item, index) => (
          <circle key={`anchor-${index}`} className="player__mask-anchor"
            cx={item.x * width} cy={item.y * height} r={Math.max(5, handleRadius * 0.7)}
            tabIndex={editable ? 0 : -1} role="button"
            aria-label={`编辑钢笔路径点 ${index + 1}`}
            aria-pressed={selectedAnchor === index}
            onFocus={() => setSelectedAnchor(index)}
            onPointerDown={(event) => { setSelectedAnchor(index); begin(event, "point", index); }}
            onKeyDown={(event) => keyMove(event, "point", index)} />
        )) : null}
        {!drawing && editAnchors && current.shape === "freehand" &&
          current.pathMode === "bezier" && selectedAnchor !== null && current.points[selectedAnchor] ? (() => {
            const index = selectedAnchor;
            const anchor = bezierHandles(current.points)[index];
            const inHandle = anchor.inHandle!;
            const outHandle = anchor.outHandle!;
            const radius = Math.max(7, handleRadius * 0.72);
            return (
              <g key={`tangent-${index}`} aria-label={`钢笔路径点 ${index + 1} 的贝塞尔切线`}>
                <line className="player__mask-tangent" x1={inHandle.x * width} y1={inHandle.y * height}
                  x2={anchor.x * width} y2={anchor.y * height} />
                <line className="player__mask-tangent" x1={anchor.x * width} y1={anchor.y * height}
                  x2={outHandle.x * width} y2={outHandle.y * height} />
                <circle className="player__mask-tangent-handle"
                  cx={inHandle.x * width} cy={inHandle.y * height} r={radius}
                  tabIndex={editable ? 0 : -1} role="button"
                  aria-label={`编辑贝塞尔手柄 ${index + 1} 入柄`}
                  onPointerDown={(event) => begin(event, "handle", index, "inHandle")}
                  onKeyDown={(event) => keyMove(event, "handle", index, "inHandle")} />
                <circle className="player__mask-tangent-handle"
                  cx={outHandle.x * width} cy={outHandle.y * height} r={radius}
                  tabIndex={editable ? 0 : -1} role="button"
                  aria-label={`编辑贝塞尔手柄 ${index + 1} 出柄`}
                  onPointerDown={(event) => begin(event, "handle", index, "outHandle")}
                  onKeyDown={(event) => keyMove(event, "handle", index, "outHandle")} />
              </g>
            );
          })() : null}
        {dragging ? <text className="player__mask-size" x={box.x * width}
          y={Math.max(18, box.y * height - 8)}>
          {current.shape === "text" ? `字号 ${Math.round(current.fontSize * 100)}%`
            : current.shape === "freehand" ? `${current.points.length} 个路径点`
            : `${Math.round(current.w * 100)}% × ${Math.round(current.h * 100)}%`}
        </text> : null}
      </svg>
    </>
  );
}
