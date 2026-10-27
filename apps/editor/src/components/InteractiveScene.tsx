/** Actual media pixels, composited locally while a gesture is still in progress. */
import { useEffect, useRef, useState } from "react";
import type { Clip, Project, Track } from "../types/api";
import { useEditor } from "../store/editor";
import { pendingCommands, useInteractionProject, useInteractionVersion } from "../store/interactive";
import { rationalToSecs } from "../lib/rational";
import { evaluateKeyframes } from "../lib/keyframes";
import { curvePoints, curveSourceOffset } from "../lib/speedCurve";
import { previewContentKey } from "../lib/previewPreparation";
import { tr } from "../i18n";

type Layer = { clip: Clip; track: Track; x: number; y: number; scale: number; rotation: number; opacity: number; sticker: boolean };
type Media = { key: string; source?: HTMLImageElement | HTMLVideoElement; processed: boolean; ready: boolean;
  request?: AbortController; timer?: number; activeSignature?: string; completed?: string; pending?: { signature: string; scope: string; body: object }; busy?: boolean;
  scope?: string; url?: string; abort: AbortController; disposed?: boolean; error?: string };
const TRANSFORM = "cutvoke.transform";
const AUDIO = new Set(["cutvoke.fx.equalizer", "cutvoke.fx.compressor", "cutvoke.fx.loudnorm", "cutvoke.fx.pan", "cutvoke.fx.sidechainDuck"]);
const IMAGE = /\.(png|jpe?g|webp|gif|bmp|svg|avif)(?:$|\?)/i;
function fullScene(project: Project, time: number) {
  if (project.sequence.tracks.some(track => track.clips.some(clip => clip.compositing || clip.role === "adjustment" || clip.sequenceRef || clip.propertyLinks || clip.motionPath))) return true;
  return !!project.sequence.multicam || project.sequence.tracks.some(track => track.visible !== false && track.clips.some(clip => {
    if (clip.hidden) return false;
    const start = rationalToSecs(clip.timelineStart), end = rationalToSecs(clip.timelineEnd);
    return start <= time && time < end && (!!clip.nested || clip.effects?.some(e => e.enabled !== false && e.effectId.startsWith("cutvoke.transition.") && time < start + Number(e.params?.duration ?? 1)));
  }));
}
const special = (clip: Clip) => !!clip.freezeAt || rationalToSecs(clip.speed) < 0 || clip.effects?.some(e => e.enabled !== false && e.effectId !== TRANSFORM && !AUDIO.has(e.effectId));

export function sourceTime(clip: Clip, time: number): number {
  if (clip.freezeAt) return rationalToSecs(clip.freezeAt);
  const local = Math.max(0, time - rationalToSecs(clip.timelineStart));
  const points = curvePoints(clip.speedCurve);
  return rationalToSecs(clip.sourceStart) + (points && clip.speedCurve
    ? curveSourceOffset(local, rationalToSecs(clip.speedCurve.sourceDuration), points)
    : local * (rationalToSecs(clip.speed) || 1));
}

export function sourceRate(clip: Clip, time: number) {
  const points = curvePoints(clip.speedCurve);
  if (!points || !clip.speedCurve) return rationalToSecs(clip.speed) || 1;
  const fraction = (sourceTime(clip, time) - rationalToSecs(clip.sourceStart)) / rationalToSecs(clip.speedCurve.sourceDuration);
  for (let i = 0; i < points.length - 1; i++) if (fraction <= points[i + 1].at) {
    const a = points[i], b = points[i + 1];
    return a.speed + (b.speed - a.speed) * (fraction - a.at) / (b.at - a.at);
  }
  return points[points.length - 1].speed;
}

function layersAt(project: Project, time: number): Layer[] {
  const { width, height } = project.sequence;
  return project.sequence.tracks.filter(t => t.kind === "video" && t.visible !== false && (!project.sequence.multicam?.activeTrackId || t.id === project.sequence.multicam.activeTrackId))
    .sort((a, b) => Number(a.role === "sticker") - Number(b.role === "sticker"))
    .flatMap(track => track.clips.filter(c => !c.hidden && rationalToSecs(c.timelineStart) <= time && time < rationalToSecs(c.timelineEnd))
      .map(clip => {
        const p = clip.effects?.find(e => e.effectId === TRANSFORM && e.enabled !== false)?.params || {};
        const pos = p.position as { x?: number; y?: number } | undefined;
        const local = time - rationalToSecs(clip.timelineStart), sticker = track.role === "sticker" || clip.role === "sticker";
        const value = (key: string, fallback: number) => evaluateKeyframes(clip.keyframes?.[key], local, fallback);
        return { clip, track, sticker, x: value("x", Number(pos?.x || 0)), y: value("y", Number(pos?.y || 0)),
          scale: value("scale", Number(p.scale ?? (sticker ? .28 : 1))), rotation: value("rotation", Number(p.rotation || 0)),
          opacity: value("opacity", Number(p.opacity ?? 1)), width, height };
      }));
}

function transform(ctx: CanvasRenderingContext2D, layer: Layer, width: number, height: number) {
  const unit = Math.min(width, height);
  const cx = layer.sticker ? layer.x + unit * layer.scale / 2 : width / 2 + layer.x;
  const cy = layer.sticker ? layer.y + unit * layer.scale / 2 : height / 2 + layer.y;
  ctx.translate(cx, cy); ctx.rotate(layer.rotation * Math.PI / 180); ctx.scale(layer.scale, layer.scale);
  ctx.translate(layer.sticker ? -unit / 2 : -width / 2, layer.sticker ? -unit / 2 : -height / 2);
}

export function InteractiveScene({ playing, onReady }: { playing: boolean; onReady(ready: boolean): void }) {
  const { state, dispatch } = useEditor();
  const preview = useInteractionProject(state.project);
  const tick = useInteractionVersion();
  const canvas = useRef<HTMLCanvasElement>(null);
  const media = useRef(new Map<string, Media>());
  const unsupported = useRef(new Set<string>());
  const [retry, setRetry] = useState(0);
  const latest = useRef({ preview, time: state.playhead, playing });
  latest.current = { preview, time: state.playhead, playing };
  const [ready, setReady] = useState(false);
  const [calculating, setCalculating] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const projectId = state.currentId;
  const client = useRef(crypto.randomUUID());
  const serial = useRef(0);
  const dispose = (entry: Media) => {
    entry.disposed = true; entry.abort.abort(); entry.request?.abort(); clearTimeout(entry.timer);
    if (entry.source instanceof HTMLVideoElement) { entry.source.pause(); entry.source.removeAttribute("src"); entry.source.load(); }
    if (entry.url) URL.revokeObjectURL(entry.url);
  };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; onReady(false); for (const entry of media.current.values()) dispose(entry); media.current.clear(); };
  }, [projectId, onReady]);
  const pump = async (entry: Media) => {
    if (entry.busy || !entry.pending || entry.disposed) return;
    const target = entry.pending;
    if (target.signature === entry.completed) return;
    entry.busy = true; entry.activeSignature = target.signature;
    const request = new AbortController(); entry.request = request;
    let url: string | undefined;
    try {
      const response = await fetch(`/api/v1/projects/${encodeURIComponent(projectId!)}/interactive-frame`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...target.body, client: `${client.current}:${(target.body as { clipId?: string }).clipId || "scene"}`, serial: ++serial.current }), signal: request.signal });
      if (!response.ok) { const body = await response.json(); throw new Error(body.error?.message || response.statusText); }
      url = URL.createObjectURL(await response.blob());
      const image = new Image(); image.src = url; await image.decode();
      if (request.signal.aborted || entry.disposed || target.scope !== entry.scope || target.signature !== entry.pending?.signature) return;
      if (entry.url) URL.revokeObjectURL(entry.url);
      entry.source = image; entry.url = url; entry.ready = true; entry.error = undefined;
      url = undefined;
      entry.completed = target.signature; if (mounted.current) setError("");
    } catch (failure) {
      if (!request.signal.aborted && !entry.disposed && target.signature === entry.pending?.signature) { entry.error = String(failure); if (mounted.current) setError(String(failure)); }
      if (!request.signal.aborted) entry.completed = target.signature;
    } finally {
      if (url) URL.revokeObjectURL(url);
      entry.busy = false;
      entry.request = undefined;
      // An aborted request can become the latest target again (A -> B -> A).
      // Keep pumping until that target has actually completed.
      if (!entry.disposed && entry.pending?.signature !== entry.completed) schedule(entry);
    }
  };
  const schedule = (entry: Media) => {
    clearTimeout(entry.timer);
    if (entry.activeSignature === entry.pending?.signature && entry.busy) return;
    entry.timer = window.setTimeout(() => { entry.request?.abort(); void pump(entry); }, 60);
  };
  useEffect(() => {
    if (!preview || state.opening) return;
    const layers = layersAt(preview, state.playhead);
    const whole = fullScene(preview, state.playhead);
    const retained = new Set(whole ? ["$scene"] : layers.map(layer => layer.clip.id));
    for (const [id, entry] of media.current) if (!retained.has(id)) { dispose(entry); media.current.delete(id); }
    let computing = false;
    if (whole) {
      const at = Math.floor(state.playhead * rationalToSecs(preview.sequence.fps)) / rationalToSecs(preview.sequence.fps);
      let entry = media.current.get("$scene");
      if (!entry) { entry = { key: "$scene", processed: true, ready: false, abort: new AbortController() }; media.current.set("$scene", entry); }
      const scope = `${projectId}:${at}`;
      const signature = JSON.stringify([scope, previewContentKey(preview)]);
      if (entry.scope !== scope) entry.ready = false;
      entry.scope = scope;
      entry.pending = { scope, signature, body: { t: at, maximum: 1920, commands: pendingCommands(projectId!) } };
      if (playing && pendingCommands(projectId!).length) window.dispatchEvent(new Event("cutvoke:pause-for-edit"));
      if (!playing && signature !== entry.completed) { computing = true; schedule(entry); }
    }
    for (const layer of whole ? [] : layers) {
      const { clip } = layer;
      const processed = unsupported.current.has(clip.assetRef.sourcePath) || !!special(clip) || !!clip.nested;
      const key = `${projectId}:${clip.assetRef.sourcePath}:${processed}`;
      let entry = media.current.get(clip.id);
      if (entry?.key !== key) {
        if (entry) dispose(entry);
        entry = { key, processed, ready: false, abort: new AbortController() };
        media.current.set(clip.id, entry);
        if (!processed) {
          const url = `/api/v1/projects/${encodeURIComponent(projectId!)}/source-media?path=${encodeURIComponent(clip.assetRef.sourcePath)}`;
          if (IMAGE.test(clip.assetRef.sourcePath)) {
            const image = new Image(); image.src = url;
            entry.source = image;
            const owned = entry; image.onload = () => { owned.ready = true; };
            image.onerror = () => { if (owned.disposed) return; owned.error = "source"; unsupported.current.add(clip.assetRef.sourcePath); if (mounted.current) setRetry(v => v + 1); };
          } else {
            const video = document.createElement("video"); video.muted = true; video.playsInline = true; video.preload = "auto";
            entry.source = video; video.src = url;
            const owned = entry; video.onloadeddata = video.onseeked = () => { owned.ready = true; };
            video.onerror = () => { if (owned.disposed) return; owned.error = "source"; unsupported.current.add(clip.assetRef.sourcePath); if (mounted.current) setRetry(v => v + 1); }; video.load();
          }
        }
      }
      if (processed) {
        if (playing && pendingCommands(projectId!).some(e => e.payload.clipId === clip.id))
          window.dispatchEvent(new Event("cutvoke:pause-for-edit"));
        if (playing) continue;
        const at = Math.floor(state.playhead * rationalToSecs(preview.sequence.fps)) / rationalToSecs(preview.sequence.fps);
        const scope = `${projectId}:${clip.id}:${at}`;
        const signature = JSON.stringify([scope, clip.effects?.filter(e => e.effectId !== TRANSFORM && !AUDIO.has(e.effectId)), clip.assetRef, clip.sourceStart, clip.speed, clip.speedCurve, clip.keyframes, clip.timelineEnd]);
        if (entry.scope !== scope) entry.ready = false;
        entry.scope = scope;
        entry.pending = { signature, scope, body: { clipId: clip.id, t: at, maximum: 1920,
          commands: pendingCommands(projectId!).filter(edit => edit.payload.clipId === clip.id) } };
        if (signature !== entry.completed) { computing = true; schedule(entry); }
      } else if (entry.source instanceof HTMLVideoElement) {
        const video = entry.source, target = sourceTime(clip, state.playhead);
        if (video.readyState >= 1 && Math.abs(video.currentTime - target) > (playing ? .18 : .015)) {
          entry.ready = false; video.currentTime = Math.min(Math.max(0, video.duration - .001), target);
        }
        video.playbackRate = Math.min(16, Math.max(.0625, sourceRate(clip, state.playhead)));
        if (playing && video.paused) void video.play().catch(() => {});
        else if (!playing) video.pause();
      }
    }
    setCalculating(computing);
  }, [preview, state.playhead, playing, projectId, state.opening, tick, retry]);
  useEffect(() => {
    let frame = 0;
    const draw = () => {
      const { preview: project, time, playing: moving } = latest.current;
      const surface = canvas.current;
      if (project && surface) {
        const layers = layersAt(project, time);
        const whole = fullScene(project, time), wholeEntry = media.current.get("$scene");
        const complete = whole ? !!wholeEntry?.ready && !wholeEntry.error && !moving : layers.every(layer => {
          const entry = media.current.get(layer.clip.id);
          return entry?.ready && !entry.error && !(moving && entry.processed);
        });
        const busy = [...media.current.values()].some(e => e.busy || e.pending && e.pending.signature !== e.completed);
        setCalculating(previous => previous === busy ? previous : busy);
        setReady(previous => previous === complete ? previous : complete);
        const ctx = surface.getContext("2d");
        if (ctx && complete) {
          const { width, height } = project.sequence;
          const factor = Math.min(1, 1920 / Math.max(width, height));
          const w = Math.round(width * factor), h = Math.round(height * factor);
          if (surface.width !== w || surface.height !== h) { surface.width = w; surface.height = h; }
          ctx.setTransform(factor, 0, 0, factor, 0, 0); ctx.globalAlpha = 1;
          ctx.fillStyle = project.sequence.backgroundColor || "#000000"; ctx.fillRect(0, 0, width, height);
          if (wholeEntry?.source && whole) ctx.drawImage(wholeEntry.source, 0, 0, width, height);
          for (const layer of whole ? [] : layers) {
            const entry = media.current.get(layer.clip.id)!;
            if (!entry.source) continue;
            const source = entry.source;
            const sw = source instanceof HTMLVideoElement ? source.videoWidth : source.naturalWidth;
            const sh = source instanceof HTMLVideoElement ? source.videoHeight : source.naturalHeight;
            if (!sw || !sh) continue;
            ctx.save(); transform(ctx, layer, width, height); ctx.globalAlpha = layer.opacity;
            if (entry.processed) ctx.drawImage(source, 0, 0, width, height);
            else {
              const dw = layer.sticker ? Math.min(width, height) : width, dh = layer.sticker ? dw : height;
              const fit = Math.min(dw / sw, dh / sh);
              ctx.drawImage(source, (dw - sw * fit) / 2, (dh - sh * fit) / 2, sw * fit, sh * fit);
            }
            ctx.restore();
          }
        }
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, []);
  useEffect(() => { onReady(ready); }, [ready, onReady]);
  const select = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!preview) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width * preview.sequence.width;
    const y = (event.clientY - bounds.top) / bounds.height * preview.sequence.height;
    const { width, height } = preview.sequence;
    for (const layer of layersAt(preview, state.playhead).reverse()) {
      if (layer.opacity <= .001) continue;
      const unit = Math.min(width, height), centerX = layer.sticker ? layer.x + unit * layer.scale / 2 : width / 2 + layer.x;
      const centerY = layer.sticker ? layer.y + unit * layer.scale / 2 : height / 2 + layer.y;
      const angle = -layer.rotation * Math.PI / 180;
      const localX = ((x - centerX) * Math.cos(angle) - (y - centerY) * Math.sin(angle)) / layer.scale + (layer.sticker ? unit : width) / 2;
      const localY = ((x - centerX) * Math.sin(angle) + (y - centerY) * Math.cos(angle)) / layer.scale + (layer.sticker ? unit : height) / 2;
      const w = layer.sticker ? unit : width, h = layer.sticker ? unit : height;
      if (localX < 0 || localY < 0 || localX >= w || localY >= h) continue;
      const entry = media.current.get(layer.clip.id), source = entry?.source;
      if (source) {
        const sw = source instanceof HTMLVideoElement ? source.videoWidth : source.naturalWidth;
        const sh = source instanceof HTMLVideoElement ? source.videoHeight : source.naturalHeight;
        const fit = entry.processed ? 1 : Math.min(w / sw, h / sh);
        const sx = entry.processed ? localX / w * sw : (localX - (w - sw * fit) / 2) / fit;
        const sy = entry.processed ? localY / h * sh : (localY - (h - sh * fit) / 2) / fit;
        if (sx < 0 || sy < 0 || sx >= sw || sy >= sh) continue;
        const sample = document.createElement("canvas"); sample.width = sample.height = 1;
        const context = sample.getContext("2d", { willReadFrequently: true });
        if (context) { context.drawImage(source, Math.floor(sx), Math.floor(sy), 1, 1, 0, 0, 1, 1); if (context.getImageData(0, 0, 1, 1).data[3] < 8) continue; }
      }
      event.stopPropagation();
      dispatch({ type: "SELECTION_SET", selection: { trackId: layer.track.id, clipId: layer.clip.id } });
      window.dispatchEvent(new Event("cutvoke:select-visual"));
      return;
    }
  };
  return <>
    <canvas ref={canvas} className="player__interactive-scene" data-ready={ready} aria-label={tr("预览画面")}
      style={{ opacity: ready ? 1 : 0, pointerEvents: ready ? "auto" : "none" }} onPointerDown={select} />
    {calculating && !playing ? <span className="player__interactive-status" role="status">{tr("正在准备预览…")}</span> : null}
    {error ? <span className="player__interactive-status" role="alert">{tr("预览渲染失败：{0}", error)}
      <button type="button" className="tool-btn" onClick={() => {
        for (const entry of media.current.values()) if (entry.error) { entry.error = undefined; entry.completed = undefined; }
        setError(""); setRetry(value => value + 1);
      }}>{tr("重试预览")}</button></span> : null}
  </>;
}
