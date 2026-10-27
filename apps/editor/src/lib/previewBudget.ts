import { useSyncExternalStore } from "react";
export interface PreviewBudget { enabled: boolean; continuous: boolean; aheadSeconds: number; delayMs: number; wallSeconds: number; range?: { projectId: string; start: number; end: number } }
const KEY = "cutvoke.preview-budget.v1", EVENT = "cutvoke:preview-budget";
export const DEFAULT_PREVIEW_BUDGET: PreviewBudget = { enabled: true, continuous: true, aheadSeconds: 6, delayMs: 600, wallSeconds: 15 };
let cached: PreviewBudget | undefined;
export function previewBudget(): PreviewBudget {
  if (cached) return cached;
  let saved: Partial<PreviewBudget> = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { /* Use bounded defaults. */ }
  const bounded = (value: unknown, defaultValue: number, low: number, high: number) => typeof value === "number" && Number.isFinite(value) ? Math.min(high, Math.max(low, value)) : defaultValue;
  return cached = { enabled: saved.enabled !== false, continuous: saved.continuous !== false,
    aheadSeconds: bounded(saved.aheadSeconds, 6, 0, 30), delayMs: bounded(saved.delayMs, 600, 200, 10000), wallSeconds: bounded(saved.wallSeconds, 15, 1, 120),
    range: saved.range && typeof saved.range.projectId === "string" && Number.isFinite(saved.range.start) && Number.isFinite(saved.range.end) && saved.range.start >= 0 && saved.range.end > saved.range.start ? saved.range : undefined };
}
export function savePreviewBudget(value: PreviewBudget) { cached = undefined; try { localStorage.setItem(KEY, JSON.stringify(value)); } catch { /* Session setting still applies. */ }
  cached = { ...value }; window.dispatchEvent(new Event(EVENT)); }
const subscribe = (listener: () => void) => { window.addEventListener(EVENT, listener); return () => window.removeEventListener(EVENT, listener); };
export function usePreviewBudget() { return useSyncExternalStore(subscribe, previewBudget, () => DEFAULT_PREVIEW_BUDGET); }
