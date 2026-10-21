import type { SpeedCurve } from "../types/api";
import { rationalToSecs } from "./rational";

export type SpeedPointDraft = { at: number; speed: number };

export function curvePoints(curve: SpeedCurve | null | undefined): SpeedPointDraft[] | null {
  return curve?.points.map((point) => ({
    at: rationalToSecs(point.at), speed: rationalToSecs(point.speed),
  })) || null;
}

export function curveElapsed(sourceSeconds: number, sourceDuration: number,
                             points: SpeedPointDraft[]): number {
  const target = Math.max(0, Math.min(sourceDuration, sourceSeconds));
  let elapsed = 0;
  for (let index = 0; index < points.length - 1; index++) {
    const left = points[index];
    const right = points[index + 1];
    const start = left.at * sourceDuration;
    const span = (right.at - left.at) * sourceDuration;
    const used = Math.max(0, Math.min(span, target - start));
    const slope = (right.speed - left.speed) / span;
    elapsed += Math.abs(slope) < 1e-10
      ? used / left.speed
      : Math.log1p(slope * used / left.speed) / slope;
  }
  return elapsed;
}

export function curveSourceOffset(elapsed: number, sourceDuration: number,
                                  points: SpeedPointDraft[]): number {
  let low = 0;
  let high = sourceDuration;
  const goal = Math.max(0, Math.min(curveElapsed(sourceDuration, sourceDuration, points), elapsed));
  for (let index = 0; index < 45; index++) {
    const middle = (low + high) / 2;
    if (curveElapsed(middle, sourceDuration, points) < goal) low = middle;
    else high = middle;
  }
  return (low + high) / 2;
}

export function validCurvePoints(points: SpeedPointDraft[]): boolean {
  return points.length >= 2 && points.length <= 12 && points[0].at === 0 &&
    points[points.length - 1].at === 1 && points.every((point, index) =>
      Number.isFinite(point.at) && Number.isFinite(point.speed) &&
      point.speed >= 0.1 && point.speed <= 8 &&
      (index === 0 || point.at > points[index - 1].at));
}
