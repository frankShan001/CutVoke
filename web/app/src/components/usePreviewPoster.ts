import { useEffect, useState } from "react";
import { fetchPreviewFrame } from "../lib/mediaApi";

/** Retain the composed playhead frame while continuous preview is preparing. */
export function usePreviewPoster(opts: {
  projectId: string | null;
  identity: string;
  time: number;
  enabled: boolean;
}) {
  const { projectId, identity, time, enabled } = opts;
  const key = `${identity}:${time}`;
  const [poster, setPoster] = useState<{ key: string; url: string } | null>(null);
  useEffect(() => {
    setPoster(null);
    if (!enabled || !projectId || !identity) return;
    const controller = new AbortController();
    let alive = true;
    let objectUrl: string | null = null;
    // A cancelled HTTP request can still leave FFmpeg rendering on the server.
    // Wait for the pointer to settle before asking for a composed poster.
    const timer = window.setTimeout(() => void fetchPreviewFrame({ projectId, t: time, width: 640, height: 360,
      signal: controller.signal }).then((result) => {
      if (result.kind !== "frame") return;
      if (!alive) {
        URL.revokeObjectURL(result.url);
        return;
      }
      objectUrl = result.url;
      setPoster({ key, url: result.url });
    }), 100);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [projectId, identity, time, key, enabled]);
  return enabled && poster?.key === key ? poster.url : null;
}
