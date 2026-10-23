import type { Keyframe } from "../types/api";
import { rationalToSecs } from "./rational";

/** Evaluate a clip-local numeric keyframe lane using the renderer's easing rules. */
export function evaluateKeyframes(
  keyframes: readonly Keyframe[] | undefined,
  localTime: number,
  fallback: number,
): number {
  const sorted = [...(keyframes ?? [])]
    .sort((left, right) => rationalToSecs(left.time) - rationalToSecs(right.time));
  if (sorted.length === 0 || !Number.isFinite(localTime)) return fallback;
  const valueAt = (index: number) => Number(sorted[index].value);
  const first = valueAt(0);
  if (!Number.isFinite(first)) return fallback;
  const firstTime = rationalToSecs(sorted[0].time);
  if (localTime <= firstTime) return first;
  for (let index = 0; index < sorted.length - 1; index += 1) {
    const left = sorted[index];
    const right = sorted[index + 1];
    const leftTime = rationalToSecs(left.time);
    const rightTime = rationalToSecs(right.time);
    if (localTime > rightTime) continue;
    const leftValue = valueAt(index);
    const rightValue = valueAt(index + 1);
    if (!Number.isFinite(leftValue) || !Number.isFinite(rightValue)) return fallback;
    if (rightTime <= leftTime) return rightValue;
    let progress = Math.max(0, Math.min(1, (localTime - leftTime) / (rightTime - leftTime)));
    if (left.interpolation === "ease-in") progress *= progress;
    else if (left.interpolation === "ease-out") progress = 1 - (1 - progress) ** 2;
    else if (left.interpolation === "cubic-bezier" && left.bezier) {
      const [x1,y1,x2,y2] = left.bezier;
      const coordinate = (u: number,a: number,b: number) => 3*(1-u)**2*u*a+3*(1-u)*u*u*b+u**3;
      let low=0, high=1;
      for (let i=0;i<40;i+=1) {
        const u=(low+high)/2;
        if (coordinate(u,x1,x2)<progress) low=u; else high=u;
      }
      progress=coordinate((low+high)/2,y1,y2);
    }
    return leftValue + (rightValue - leftValue) * progress;
  }
  const last = valueAt(sorted.length - 1);
  return Number.isFinite(last) ? last : fallback;
}
