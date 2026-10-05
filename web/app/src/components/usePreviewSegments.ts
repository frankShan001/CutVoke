import { useEffect, useMemo, useRef, useState } from "react";
import { PreviewSegmentCache, type CachedPreviewSegment } from "../lib/previewSegmentCache";

export function usePreviewSegments(scope: string, source: string, enabled: boolean) {
  const cache = useMemo(() => new PreviewSegmentCache(), [scope]);
  const lifetimes = useRef(new WeakMap<PreviewSegmentCache, object>());
  const [result, setResult] = useState<{ cache: PreviewSegmentCache; source: string;
    media?: CachedPreviewSegment; error?: string } | null>(null);
  useEffect(() => {
    const token = {};
    lifetimes.current.set(cache, token);
    // StrictMode replays setup/cleanup. Dispose only after a real departure.
    return () => queueMicrotask(() => {
      if (lifetimes.current.get(cache) === token) cache.dispose();
    });
  }, [cache]);
  useEffect(() => {
    if (!enabled || !source) return;
    let alive = true;
    const lease = cache.acquire(source);
    void lease.promise.then(media => {
      if (alive) setResult({ cache, source, media });
    }, error => {
      if (alive && !(error instanceof DOMException && error.name === "AbortError")) {
        setResult({ cache, source, error: error instanceof Error ? error.message : "预览视频段加载失败" });
      }
    });
    return () => { alive = false; lease.release(); };
  }, [cache, source, enabled]);
  const current = result?.cache === cache && result.source === source ? result : null;
  return { cache, media: enabled ? cache.peek(source) || current?.media || null : null,
    error: enabled ? current?.error || null : null };
}
