import { useEffect, useRef } from "react";
import { useEditor } from "../store/editor";
import { pendingCommands, useInteractionProject } from "../store/interactive";
import { probeMedia } from "../lib/mediaApi";
import { rationalToSecs } from "../lib/rational";
import { sourceTime } from "./InteractiveScene";

const AUDIO_FX = new Set(["cutvoke.fx.equalizer", "cutvoke.fx.compressor", "cutvoke.fx.loudnorm", "cutvoke.fx.pan", "cutvoke.fx.sidechainDuck"]);
type Voice = { element: HTMLAudioElement; gain?: GainNode; analyser?: AnalyserNode; controller: AbortController;
  signature: string; prepared: boolean; ready: boolean; url?: string; disposed?: boolean; failed?: boolean; hasAudio?: boolean };

/** One gain envelope per clip. Moving a fader never re-encodes the mixed video. */
export function InteractiveAudio({ playing, onReady }: { playing: boolean; onReady(ready: boolean): void }) {
  const { state } = useEditor();
  const project = useInteractionProject(state.project);
  const context = useRef<AudioContext | null>(null);
  const client = useRef(crypto.randomUUID()), serial = useRef(0);
  const voices = useRef(new Map<string, Voice>());
  const meter = useRef<HTMLOutputElement>(null);
  const latest = useRef({ project, time: state.playhead, playing, onReady });
  latest.current = { project, time: state.playhead, playing, onReady };
  useEffect(() => {
    const unlock = () => { if (context.current?.state === "suspended") void context.current.resume(); };
    document.addEventListener("pointerdown", unlock);
    document.addEventListener("keydown", unlock);
    return () => { document.removeEventListener("pointerdown", unlock); document.removeEventListener("keydown", unlock); };
  }, []);
  useEffect(() => {
    let frame = 0, disposed = false;
    const update = () => {
      const { project: snapshot, time, playing: running, onReady: ready } = latest.current;
      const activeIds = new Set<string>();
      let hasAudio = false, allReady = true;
      // A sidechain needs the other clips' key streams. Keep the complete
      // server mix audible instead of preparing an isolated, unducked clip.
      const sidechainMix = snapshot?.sequence.tracks.some(track => track.clips.some(clip =>
        clip.effects?.some(effect => effect.enabled !== false && effect.effectId === "cutvoke.fx.sidechainDuck")));
      const unsupportedMix = snapshot?.sequence.tracks.some(track => track.visible !== false && !track.muted &&
        track.clips.some(clip => !clip.hidden && (clip.nested || clip.freezeAt) && rationalToSecs(clip.timelineStart) <= time && time < rationalToSecs(clip.timelineEnd)));
      for (const track of sidechainMix ? [] : snapshot?.sequence.tracks || []) {
        if (!['audio', 'video'].includes(track.kind) || track.role === "sticker" || track.visible === false || track.muted) continue;
        for (const clip of track.clips) {
          if (clip.hidden || clip.role === "sticker" || /\.(png|jpe?g|webp|gif|svg|bmp)$/i.test(clip.assetRef.sourcePath)) continue;
          const start = rationalToSecs(clip.timelineStart), end = rationalToSecs(clip.timelineEnd);
          if (time < start - 2 || time >= end) continue;
          activeIds.add(clip.id);
          const effects = clip.effects?.filter(e => e.enabled !== false && AUDIO_FX.has(e.effectId)) || [];
          const pitch = (clip as typeof clip & { pitch?: typeof clip.speed }).pitch;
          const prepared = effects.length > 0 || !!clip.speedCurve || rationalToSecs(clip.speed) < 0 || Math.abs((rationalToSecs(pitch) || 1) - 1) > 1e-6;
          const signature = JSON.stringify([snapshot!.projectId, clip.assetRef, effects, pitch, clip.preservePitch, prepared ? clip.sourceStart : null, prepared ? [clip.speed, clip.speedCurve, clip.timelineStart, clip.timelineEnd] : null]);
          let voice = voices.current.get(clip.id);
          if (voice && signature !== voice.signature) {
            voice.element.pause(); voice.disposed = true; voice.controller.abort(); voice.gain?.disconnect();
            if (voice.url) URL.revokeObjectURL(voice.url);
            voices.current.delete(clip.id); voice = undefined;
          }
          if (!voice) {
            const element = new Audio(); element.preload = "auto";
            voice = { element, controller: new AbortController(), signature, prepared, ready: false, hasAudio: track.kind === "audio" ? true : undefined };
            const owned = voice;
            if (track.kind === "video") void probeMedia(clip.assetRef.sourcePath).then(result => {
              if (!owned.disposed && result.kind === "ok") owned.hasAudio = result.data.has_audio;
            }).catch(() => {});
            element.oncanplay = () => { owned.ready = true; };
            // Silent video sources legitimately have no audio stream.
            element.onerror = () => { owned.failed = true; owned.ready = true; };
            if (!context.current) context.current = new AudioContext();
            const source = context.current.createMediaElementSource(element);
            const gain = context.current.createGain(), analyser = context.current.createAnalyser();
            analyser.fftSize = 256;
            source.connect(gain).connect(analyser).connect(context.current.destination);
            owned.gain = gain; owned.analyser = analyser;
            voices.current.set(clip.id, voice);
            if (prepared) {
              void fetch(`/api/v1/projects/${encodeURIComponent(snapshot!.projectId)}/interactive-audio`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ clipId: clip.id, client: `audio:${client.current}:${clip.id}`, serial: ++serial.current, commands: pendingCommands(snapshot!.projectId).filter(e =>
                  e.payload.clipId === clip.id && ["effect.update", "effect.add", "effect.bypass", "clip.audio"].includes(e.type)) }), signal: owned.controller.signal,
              }).then(async response => {
                if (!response.ok) throw new Error("audio preview");
                const url = URL.createObjectURL(await response.blob());
                if (owned.disposed) { URL.revokeObjectURL(url); return; }
                owned.url = url; element.src = url; element.load();
              }).catch(() => { if (!owned.disposed) owned.failed = true; });
            } else element.src = `/api/v1/projects/${encodeURIComponent(snapshot!.projectId)}/source-media?path=${encodeURIComponent(clip.assetRef.sourcePath)}`;
          }
          if (time < start) { voice.element.pause(); continue; }
          if (voice.hasAudio === false) { voice.element.pause(); continue; }
          hasAudio = true;
          if (!voice.ready || voice.failed) { allReady = false; continue; }
          const element = voice.element;
          element.preservesPitch = clip.preservePitch !== false;
          const target = voice.prepared ? time - start : sourceTime(clip, time);
          if (element.readyState >= 1 && Math.abs(element.currentTime - target) > (running ? .14 : .02)) element.currentTime = Math.min(Math.max(0, element.duration - .001), target);
          element.playbackRate = voice.prepared ? 1 : Math.min(16, Math.max(.0625, rationalToSecs(clip.speed) || 1));
          const fadeIn = rationalToSecs(clip.fadeIn) || 0, fadeOut = rationalToSecs(clip.fadeOut) || 0;
          const volume = clip.volume ? rationalToSecs(clip.volume) : 1;
          const envelope = Math.min(1, fadeIn > 0 ? (time - start) / fadeIn : 1, fadeOut > 0 ? (end - time) / fadeOut : 1);
          voice.gain?.gain.setValueAtTime(Math.max(0, volume * envelope), context.current!.currentTime);
          if (running && element.paused) {
            void context.current?.resume(); void element.play().catch(() => {});
          } else if (!running) element.pause();
        }
      }
      for (const [id, voice] of voices.current) if (!activeIds.has(id)) {
        voice.disposed = true; voice.controller.abort(); voice.element.pause(); voice.gain?.disconnect();
        voice.element.removeAttribute("src"); voice.element.load(); if (voice.url) URL.revokeObjectURL(voice.url); voices.current.delete(id);
      }
      const enabled = hasAudio && allReady && !unsupportedMix && !sidechainMix;
      // Never play a partial native mix together with the server mix.
      if (!enabled) for (const voice of voices.current.values()) voice.element.pause();
      ready(enabled);
      if (meter.current) {
        let energy = 0;
        for (const voice of voices.current.values()) if (voice.analyser) {
          const samples = new Float32Array(256); voice.analyser.getFloatTimeDomainData(samples);
          energy += samples.reduce((sum, value) => sum + value * value, 0) / samples.length;
        }
        meter.current.dataset.rms = String(Math.sqrt(energy));
        meter.current.dataset.ready = String(enabled);
      }
      if (!disposed) frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => {
      disposed = true; cancelAnimationFrame(frame);
      for (const voice of voices.current.values()) { voice.disposed = true; voice.controller.abort(); voice.element.pause(); voice.gain?.disconnect(); if (voice.url) URL.revokeObjectURL(voice.url); }
      voices.current.clear(); void context.current?.close(); context.current = null;
    };
  }, [state.currentId]);
  return <output ref={meter} data-testid="live-audio-meter" hidden />;
}
