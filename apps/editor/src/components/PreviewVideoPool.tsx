import { useLayoutEffect, useRef, useState, type VideoHTMLAttributes } from "react";

interface VideoEntry { source: string; src: string; bytes: number }
interface Props {
  source: string;
  src: string;
  bytes: number;
  identity: string;
  visible: boolean;
  bindVideo: (video: HTMLVideoElement | null) => void;
  events: VideoHTMLAttributes<HTMLVideoElement>;
}

/** Keep the most recent decoders mounted. Compressed segments have a larger
 * separate cache, so evicting a decoder does not require a server download. */
export function PreviewVideoPool({ source, src, bytes, identity, visible, bindVideo, events }: Props) {
  const [entries, setEntries] = useState<VideoEntry[]>([]);
  const nodes = useRef(new Map<string, HTMLVideoElement>());
  const refs = useRef(new Map<string, (video: HTMLVideoElement | null) => void>());
  useLayoutEffect(() => () => bindVideo(null), [bindVideo]);
  useLayoutEffect(() => {
    if (!src) return;
    setEntries(previous => {
      const next = [...previous.filter(e => e.source !== source), { source, src, bytes }];
      let total = next.reduce((sum, e) => sum + e.bytes, 0);
      while (next.length > 1 && (next.length > 8 || total > 64 * 1024 * 1024)) total -= next.shift()!.bytes;
      if (previous.length === next.length && previous.every((e, i) => e.source === next[i].source && e.src === next[i].src)) return previous;
      return next;
    });
  }, [source, src, bytes]);
  useLayoutEffect(() => {
    const current = src ? nodes.current.get(source) || null : null;
    bindVideo(current);
    for (const video of nodes.current.values()) if (video !== current) video.pause();
    const retained = new Set(entries.map(e => e.source));
    for (const key of refs.current.keys()) if (!retained.has(key)) refs.current.delete(key);
  }, [source, src, entries, bindVideo]);
  return <>{entries.map(entry => {
    let ref = refs.current.get(entry.source);
    if (!ref) {
      ref = video => { if (video) nodes.current.set(entry.source, video); else nodes.current.delete(entry.source); };
      refs.current.set(entry.source, ref);
    }
    const active = entry.source === source && !!src;
    return <video {...events} key={entry.source} ref={ref} src={entry.src}
      className={active ? "player__video" : "player__cached-video"}
      hidden={!active} aria-hidden={!active}
      style={{ display: active ? undefined : "none", visibility: active && visible ? "visible" : "hidden" }}
      data-preview-source={entry.source} data-preview-identity={identity}
      playsInline preload="auto" />;
  })}</>;
}
