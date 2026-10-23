import { memo, useEffect, useState } from "react";
import type { Clip } from "../../types/api";
import { useEditor } from "../../store/editor";
import { rationalToSecs } from "../../lib/rational";
import { acquireTimelineMedia } from "../../lib/timelineMediaCache";
import "./clip-media-strip.css";

export const ClipMediaStrip = memo(function ClipMediaStrip({ clip, isAudio, isText, pxPerSec,
  visibleStart = 0, visibleEnd = Infinity }: { clip: Clip; isAudio: boolean; isText: boolean;
  pxPerSec: number; visibleStart?: number; visibleEnd?: number }) {
  const { state } = useEditor({ subscribeToClock: false });
  const [loaded, setLoaded] = useState<{ url: string; image: string } | null>(null);
  const [restore, setRestore] = useState(0);
  useEffect(() => {
    const handler = () => setRestore(value => value + 1);
    window.addEventListener("cutvoke:asset-restored", handler);
    return () => window.removeEventListener("cutvoke:asset-restored", handler);
  }, []);
  const start = rationalToSecs(clip.timelineStart), duration = rationalToSecs(clip.timelineEnd) - start;
  const step = 256 / pxPerSec;
  const from = Math.max(0, Math.floor((visibleStart - start) / step) * step);
  const to = Math.min(duration, Math.ceil((visibleEnd - start) / step) * step);
  const enabled = !isText && !clip.nested && !!clip.assetRef.sourcePath && duration * pxPerSec >= 24 && to > from;
  const version = JSON.stringify([clip.assetRef.sourcePath, clip.sourceStart, clip.speed, clip.speedCurve, clip.freezeAt, duration, restore]);
  const params = new URLSearchParams({ kind: isAudio ? "waveform" : "thumbnail",
    w: String(Math.max(64, Math.min(960, Math.ceil((to - from) * pxPerSec / 64) * 64))),
    from: from.toFixed(6), to: to.toFixed(6), v: version });
  const url = enabled && state.currentId
    ? `/api/v1/projects/${encodeURIComponent(state.currentId)}/clips/${encodeURIComponent(clip.id)}/timeline-media?${params}` : "";
  useEffect(() => {
    if (!url) return;
    let active = true;
    const lease = acquireTimelineMedia(url);
    void lease.promise.then(image => { if (active) setLoaded(image ? { url, image } : null); });
    return () => { active = false; lease.release(); };
  }, [url]);
  if (!url || loaded?.url !== url) return null;
  return <img className={`clip-media-strip${isAudio ? " clip-media-strip--waveform" : ""}`}
    src={loaded.image} style={{ left: from * pxPerSec, width: (to - from) * pxPerSec }}
    alt="" aria-hidden="true" draggable={false} data-testid={isAudio ? "timeline-waveform" : "timeline-thumbnails"} />;
});
