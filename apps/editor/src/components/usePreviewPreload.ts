import { useEffect } from "react";
import type { PreviewSegmentCache } from "../lib/previewSegmentCache";

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
  useEffect(() => {
    if (!enabled || !source) return;
    const leases: Array<ReturnType<PreviewSegmentCache["acquire"]>> = [];
    let stopped = false;
    const timer = window.setTimeout(() => {
      // One speculative transfer at a time leaves room for immediate seeks and
      // avoids flooding the renderer with every window of a long project.
      const prepare = async () => {
        for (let ahead = 1; ahead <= 3 && index + ahead < total && !stopped; ahead++) {
          const target = source.replace(/([?&]index=)\d+/, (_match, prefix: string) => `${prefix}${index + ahead}`);
          const lease = cache.acquire(target, true);
          leases.push(lease);
          try { await lease.promise; } catch { break; }
        }
      };
      void prepare();
    }, playing ? 0 : 600);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      for (const lease of leases) lease.release();
    };
  }, [cache, source, index, total, enabled, playing]);
}
