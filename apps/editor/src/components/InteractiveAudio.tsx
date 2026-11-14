import { useEffect, useRef } from "react";
import type { Project } from "../types/api";
import { useEditor } from "../store/editor";
import { pendingCommands, useInteractionProject } from "../store/interactive";
import { probeMedia } from "../lib/mediaApi";
import { rationalToSecs } from "../lib/rational";
import { STOP_AUDITION_AUDIO_EVENT } from "../lib/transcript";
import { NativeAudioStartup, type StartupVoice } from "../lib/nativeAudioStartup";
import { sourceTime } from "./InteractiveScene";

const AUDIO_FX = new Set(["cutvoke.fx.equalizer", "cutvoke.fx.compressor", "cutvoke.fx.loudnorm", "cutvoke.fx.pan", "cutvoke.fx.sidechainDuck"]);
/** Only source/timing/mix changes invalidate startup. Live gain envelopes and
 * unrelated project revisions must not interrupt healthy native playback. */
export function nativeAudioGeneration(project: Project | null) {
  if (!project) return null;
  return JSON.stringify([project.projectId, project.sequence.id, project.sequence.masterAudio, project.sequence.audioBuses,
    project.sequence.tracks.filter(track => ['audio', 'video'].includes(track.kind)).map(track =>
      [track.id, track.kind, track.role, track.visible, track.muted, track.audioMix, track.clips.map(clip =>
        [clip.id, clip.assetRef, clip.hidden, clip.nested, clip.freezeAt, clip.role, clip.sourceStart, clip.timelineStart,
          clip.timelineEnd, clip.speed, clip.speedCurve, (clip as typeof clip & { pitch?: typeof clip.speed }).pitch,
          clip.preservePitch, clip.audioFadeDuration, clip.audioFadeOffset, clip.keyframes?.volume,
          (clip.effects || []).filter(effect => effect.enabled !== false && AUDIO_FX.has(effect.effectId))])])]);
}
type Voice = { element: HTMLAudioElement; gain?: GainNode; analyser?: AnalyserNode; controller: AbortController;
  signature: string; prepared: boolean; ready: boolean; url?: string; disposed?: boolean; failed?: boolean; hasAudio?: boolean };

/** Observe eligible native media cursors separately from the AudioContext clock.
 * A device clock can lag one media element; audition bounds must also protect
 * the fastest eligible voice. This is not a hardware presentation timestamp. */
export function nativeAudioPosition(project: Project | null, voices: Map<string, Voice>): number | null {
  let position: number | null = null;
  for (const track of project?.sequence.tracks || []) for (const clip of track.clips) {
    const voice = voices.get(clip.id);
    if (!voice || voice.failed || voice.disposed || !voice.ready || voice.element.seeking || (voice.gain?.gain.value ?? 1) <= 0 || voice.element.paused || voice.element.muted || voice.element.readyState < 1) continue;
    const speed = rationalToSecs(clip.speed) || 1;
    const offset = voice.prepared ? voice.element.currentTime : (voice.element.currentTime - rationalToSecs(clip.sourceStart)) / speed;
    const time = rationalToSecs(clip.timelineStart) + offset;
    if (Number.isFinite(time)) position = Math.max(position ?? time, time);
  }
  return position;
}

/** One gain envelope per clip. Moving a fader never re-encodes the mixed video. */
export function InteractiveAudio({ playing, transportMode, onReady, playbackClock, onClockSource, onPositionSource }: {
  playing: boolean; transportMode: "image" | "video"; onReady(ready: boolean): void;
  playbackClock(initialize?: boolean, reset?: boolean): { time: number; playing: boolean } | null;
  onClockSource(clock: (() => number) | null): void;
  onPositionSource(position: (() => number | null) | null): void;
}) {
  const { state } = useEditor();
  const project = useInteractionProject(state.project);
  const context = useRef<AudioContext | null>(null);
  const client = useRef(crypto.randomUUID()), serial = useRef(0);
  const voices = useRef(new Map<string, Voice>());
  const startup = useRef(new NativeAudioStartup());
  const meter = useRef<HTMLOutputElement>(null);
  const positionGeneration = useRef<string | null>(null);
  const latest = useRef({ project, scope: state.currentId, time: state.playhead, seek: state.playheadSeekVersion, scrubbing: state.scrubbing, playing, transportMode, onReady, playbackClock, onClockSource, onPositionSource });
  latest.current = { project, scope: state.currentId, time: state.playhead, seek: state.playheadSeekVersion, scrubbing: state.scrubbing, playing, transportMode, onReady, playbackClock, onClockSource, onPositionSource };
  useEffect(() => {
    const pause = (event: Event) => {
      startup.current.cancel();
      const time = (event as CustomEvent<{ time?: number }>).detail?.time;
      for (const [id, voice] of voices.current) {
        voice.element.pause();
        // Correct the native cursor synchronously at natural range end. On
        // cancellation no time is supplied, so an explicit seek stays intact.
        if (time === undefined || !Number.isFinite(time) || voice.element.readyState < 1) continue;
        const clip = latest.current.project?.sequence.tracks.flatMap(track => track.clips).find(item => item.id === id);
        if (!clip) continue;
        if (time < rationalToSecs(clip.timelineStart) || time > rationalToSecs(clip.timelineEnd)) continue;
        const target = voice.prepared ? time - rationalToSecs(clip.timelineStart) : sourceTime(clip, time);
        const end = Number.isFinite(voice.element.duration) ? Math.max(0, voice.element.duration - .001) : target;
        voice.element.currentTime = Math.min(end, Math.max(0, target));
      }
    };
    window.addEventListener(STOP_AUDITION_AUDIO_EVENT, pause);
    return () => window.removeEventListener(STOP_AUDITION_AUDIO_EVENT, pause);
  }, []);
  useEffect(() => {
    const unlock = () => { if (context.current?.state === "suspended") void context.current.resume(); };
    document.addEventListener("pointerdown", unlock);
    document.addEventListener("keydown", unlock);
    return () => { document.removeEventListener("pointerdown", unlock); document.removeEventListener("keydown", unlock); };
  }, []);
  useEffect(() => {
    let frame = 0, disposed = false;
    const update = () => {
      const { project: snapshot, onReady: ready } = latest.current;
      // React's playhead follows presented frames and can lag the audio clock.
      // Seeking to that stale value repeatedly interrupts otherwise healthy audio.
      // Only the voice update may initialize the native clock. Earlier player
      // RAF observers must not consume the beginning of a short audition.
      const clock = latest.current.playbackClock(false);
      const time = clock?.time ?? latest.current.time;
      const running = clock?.playing ?? latest.current.playing;
      const activeIds = new Set<string>();
      const playable: StartupVoice[] = [];
      let hasAudio = false, allReady = true;
      // A sidechain needs the other clips' key streams. Keep the complete
      // server mix audible instead of preparing an isolated, unducked clip.
      const sidechainMix = !!snapshot && (Object.keys(snapshot.sequence.masterAudio || {}).length > 0 ||
        (snapshot.sequence.audioBuses || []).length > 0 || snapshot.sequence.tracks.some(track =>
          Object.keys(track.audioMix || {}).length > 0 || track.clips.some(clip =>
            !!clip.keyframes?.volume?.length || !!clip.audioFadeDuration ||
            rationalToSecs(clip.audioFadeOffset) > 0 ||
            clip.effects?.some(effect => effect.enabled !== false && effect.effectId === "cutvoke.fx.sidechainDuck"))));
      const unsupportedMix = snapshot?.sequence.tracks.some(track => track.visible !== false && !track.muted &&
        track.clips.some(clip => !clip.hidden && (clip.nested || clip.freezeAt) && rationalToSecs(clip.timelineStart) <= time && time < rationalToSecs(clip.timelineEnd)));
      for (const track of sidechainMix || unsupportedMix ? [] : snapshot?.sequence.tracks || []) {
        if (!['audio', 'video'].includes(track.kind) || track.role === "sticker" || track.visible === false || track.muted) continue;
        for (const clip of track.clips) {
          if (clip.hidden || clip.nested || clip.freezeAt || clip.role === "sticker" || /\.(png|jpe?g|webp|gif|svg|bmp)$/i.test(clip.assetRef.sourcePath)) continue;
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
            if (!context.current) {
              const audioContext = new AudioContext();
              context.current = audioContext;
              latest.current.onClockSource(() => audioContext.currentTime);
              latest.current.onPositionSource(() => {
                const now = latest.current;
                const key = JSON.stringify([now.scope, now.transportMode, nativeAudioGeneration(now.project), now.seek]);
                return now.playing && !now.scrubbing && key === positionGeneration.current
                  ? nativeAudioPosition(now.project, voices.current) : null;
              });
            }
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
          // During a window handoff/buffer stall, stop before repositioning.
          // Otherwise the old voice runs ahead while the new video is starting.
          if (!running && !startup.current.pending(element) && !element.paused) element.pause();
          element.preservesPitch = clip.preservePitch !== false;
          const target = voice.prepared ? time - start : sourceTime(clip, time);
          if (!startup.current.pending(element) && element.readyState >= 1 && Math.abs(element.currentTime - target) > (running ? .14 : .02)) element.currentTime = Math.min(Math.max(0, element.duration - .001), target);
          element.playbackRate = voice.prepared ? 1 : Math.min(16, Math.max(.0625, rationalToSecs(clip.speed) || 1));
          const fadeIn = rationalToSecs(clip.fadeIn) || 0, fadeOut = rationalToSecs(clip.fadeOut) || 0;
          const volume = clip.volume ? rationalToSecs(clip.volume) : 1;
          const envelope = Math.min(1, Math.max(0, fadeIn > 0 ? (time - start) / fadeIn : 1))
            * Math.min(1, Math.max(0, fadeOut > 0 ? (end - time) / fadeOut : 1));
          voice.gain?.gain.setValueAtTime(Math.max(0, volume * envelope), context.current!.currentTime);
          playable.push({ element, time: Math.min(Math.max(0, element.duration - .001), target) });
        }
      }
      for (const [id, voice] of voices.current) if (!activeIds.has(id)) {
        voice.disposed = true; voice.controller.abort(); voice.element.pause(); voice.gain?.disconnect();
        voice.element.removeAttribute("src"); voice.element.load(); if (voice.url) URL.revokeObjectURL(voice.url); voices.current.delete(id);
      }
      const enabled = hasAudio && allReady && !unsupportedMix && !sidechainMix;
      // Never play a partial native mix together with the server mix.
      const key = JSON.stringify([latest.current.scope, latest.current.transportMode, nativeAudioGeneration(snapshot), latest.current.seek]);
      if (!enabled || !latest.current.playing || latest.current.scrubbing) {
        startup.current.cancel();
        if (!enabled) latest.current.playbackClock(false, true);
        for (const voice of voices.current.values()) voice.element.pause();
        ready(enabled);
      } else {
        const phase = startup.current.start({ key, voices: playable, context: context.current!,
          current: () => !disposed && latest.current.playing && !latest.current.scrubbing &&
            JSON.stringify([latest.current.scope, latest.current.transportMode, nativeAudioGeneration(latest.current.project), latest.current.seek]) === key,
          begin: () => { latest.current.playbackClock(false, true); },
          commit: () => {
            const accepted = latest.current.playbackClock(true)?.playing ?? latest.current.playing;
            positionGeneration.current = accepted ? key : null;
            return accepted;
          },
          failed: () => { latest.current.playbackClock(false, true); latest.current.onReady(false); },
        });
        ready(phase !== 'failed');
      }
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
      disposed = true; startup.current.cancel(); cancelAnimationFrame(frame);
      for (const voice of voices.current.values()) { voice.disposed = true; voice.controller.abort(); voice.element.pause(); voice.gain?.disconnect(); if (voice.url) URL.revokeObjectURL(voice.url); }
      voices.current.clear(); latest.current.onClockSource(null); latest.current.onPositionSource(null);
      void context.current?.close(); context.current = null;
    };
  }, [state.currentId]);
  return <output ref={meter} data-testid="live-audio-meter" hidden />;
}
