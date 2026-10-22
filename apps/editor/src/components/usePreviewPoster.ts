import { useEffect, useRef, useState } from "react";
import { fetchPreviewFrame } from "../lib/mediaApi";

/** One request in flight; movement replaces only the pending target, never a queue. */
export function usePreviewPoster(opts: {
  projectId: string | null;
  identity: string;
  scope?: string;
  time: number;
  enabled: boolean;
  draft?: boolean;
  aspect?: number;
  onSettled?: (identity: string) => void;
}) {
  const latest = useRef(opts);
  latest.current = opts;
  const scope = opts.scope || opts.identity;
  const [poster, setPoster] = useState<{ scope: string; url: string; time: number } | null>(null);
  useEffect(() => {
    setPoster(null);
    if (!opts.enabled || !opts.projectId) return;
    let alive = true;
    let timer = 0;
    let url: string | null = null;
    let completed = "";
    const controller = new AbortController();
    const pump = async () => {
      const target = latest.current;
      const key = `${scope}:${target.time}:${target.draft}`;
      if (key !== completed) {
        completed = key;
        try {
          const maximum = target.draft ? 960 : 1920;
          const aspect = target.aspect || 16 / 9;
          const width = Math.max(2, Math.round((aspect >= 1 ? maximum : maximum * aspect) / 2) * 2);
          const height = Math.max(2, Math.round((aspect >= 1 ? maximum / aspect : maximum) / 2) * 2);
          const result = await fetchPreviewFrame({ projectId: target.projectId!, t: target.time,
            width, height, signal: controller.signal });
          if (result.kind === "frame") {
            if (!alive) { URL.revokeObjectURL(result.url); return; }
            const previous = url;
            url = result.url;
            setPoster({ scope, url, time: target.time });
            if (previous) URL.revokeObjectURL(previous);
          }
        } catch { /* Visible errors are owned by the playback fallback. */
        } finally {
          if (alive) latest.current.onSettled?.(target.identity);
        }
      }
      if (alive) timer = window.setTimeout(() => void pump(), 60);
    };
    timer = window.setTimeout(() => void pump(), 40);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [opts.projectId, scope, opts.enabled]);
  return opts.enabled && poster?.scope === scope ? poster : null;
}
