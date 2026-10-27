import { useEffect } from "react";
import type { PreviewSegmentCache } from "../lib/previewSegmentCache";
import { usePreviewBudget } from "../lib/previewBudget";

/** Build a small playback lead during an idle pause, without blocking gestures.
 * Keep the canonical cache key identical to the eventual playback request. */
export function usePreviewPreload(options: {
  cache: PreviewSegmentCache;
  source: string;
  index: number;
  total: number;
  enabled: boolean;
  playing: boolean;
}) {
  const { cache, source, index, total, enabled, playing } = options;
  const budget = usePreviewBudget();
  useEffect(() => {
    if (!enabled || !source || !budget.enabled || !budget.aheadSeconds) return;
    const leases: Array<ReturnType<PreviewSegmentCache["acquire"]>> = [];
    let stopped = false;
    const timer = window.setTimeout(() => {
      // One speculative transfer at a time leaves room for immediate seeks and
      // avoids flooding the renderer with every window of a long project.
      const prepare = async () => {
        const projectId = source.match(/\/projects\/([^/]+)/)?.[1];
        const range = budget.range && encodeURIComponent(budget.range.projectId) === projectId ? budget.range : undefined;
        for (let ahead = 1; ahead <= Math.ceil(budget.aheadSeconds / 2) && index + ahead < total && !stopped; ahead++) {
          if (range && ((index + ahead) * 2 < range.start || (index + ahead) * 2 >= range.end)) continue;
          const target = source.replace(/([?&]index=)\d+/, (_match, prefix: string) => `${prefix}${index + ahead}`);
          const lease = cache.acquire(target, true);
          leases.push(lease);
          try { await lease.promise; } catch { break; }
        }
      };
      void prepare();
    }, playing ? 0 : budget.delayMs);
    const stop = window.setTimeout(() => { stopped = true; for (const lease of leases) lease.release(); }, budget.wallSeconds * 1000 + (playing ? 0 : budget.delayMs));
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.clearTimeout(stop);
      for (const lease of leases) lease.release();
    };
  }, [cache, source, index, total, enabled, playing, budget]);
}
