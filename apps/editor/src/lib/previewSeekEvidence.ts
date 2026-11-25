/** Native-package QA evidence only. No playback decisions or extra clock reads. */
export const PREVIEW_SEEK_SITES = [
  "bind", "loadedmetadata", "loadeddata", "seeked", "loaded-other", "video-loop", "video-end",
  "external-sync", "audio-tick", "project-reset", "play-restart", "audition", "stop", "frame-step",
] as const;
type SeekSite = typeof PREVIEW_SEEK_SITES[number];
type Phase = "selection" | "bound" | "presented" | "fallback-segment" | "fallback-open" | "fallback-video";
type Evidence = {
  prepared: boolean; audioActive: boolean; nativeAudioReady: boolean; playing: boolean;
  playhead: number; decodeTime: number; reportedTime: number; audioAnchor: number;
  windowStart: number; windowEnd: number;
  offered?: boolean; matching?: boolean; retained?: boolean; warmedOffer?: boolean; ready?: boolean; holding?: boolean;
  sourceMatches?: boolean;
};
type Hooks = {
  beginSeek: (site: SeekSite, video: HTMLVideoElement, detail: Evidence) => (() => void) | undefined;
  phase: (phase: Phase, video: HTMLVideoElement | null, detail: Evidence) => void;
};
function hooks(): Hooks | undefined {
  return Object.getOwnPropertyDescriptor(window, "__cutvokeNativeSeekEvidence")?.value;
}
export function seekPreviewVideo(video: HTMLVideoElement, value: number, site: SeekSite, read: () => Evidence) {
  let restore: (() => void) | undefined;
  try {
    const observer = hooks();
    if (typeof observer?.beginSeek === "function") restore = observer.beginSeek(site, video, read());
  } catch { /* Opt-in evidence cannot change playback. */ }
  try {
    video.currentTime = value;
  } finally {
    try { restore?.(); } catch { /* Preserve native setter outcomes. */ }
  }
}
export function recordPreviewPhase(phase: Phase, video: HTMLVideoElement | null, read: () => Evidence) {
  try {
    const observer = hooks();
    if (typeof observer?.phase === "function") observer.phase(phase, video, read());
  } catch { /* Evidence never changes the playback flow. */ }
}
export function previewLoadSeekSite(type: string): SeekSite {
  return type === "loadedmetadata" || type === "loadeddata" || type === "seeked" ? type : "loaded-other";
}
