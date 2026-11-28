/** Opt-in local acceptance evidence. No clock reads, storage, or playback policy. */
export type PreviewStopReason = 'audio-clock-end' | 'video-ended' | 'video-time-end' | 'image-end';
export type PreviewStopSnapshot = {
  observedTime: number | null; decisionEnd: number | null; decisionWindowStart: number | null; decisionWindowEnd: number | null; end: number; playhead: number; reportedVideoTime: number;
  windowStart: number; windowEnd: number; audioActive: boolean; nativeAudioReady: boolean;
  audioAnchorTime: number; playing: boolean; prepared: boolean;
};
export function recordPreviewStop(reason: PreviewStopReason, snapshot: () => PreviewStopSnapshot) {
  try {
    if ((window as unknown as Record<string, unknown>).__cutvokePreviewStopEvidence !== true) return;
    if (!['audio-clock-end', 'video-ended', 'video-time-end', 'image-end'].includes(reason)) return;
    const source = snapshot();
    const detail: Record<string, number | boolean | string | null> = { reason };
    for (const key of ['observedTime', 'decisionEnd', 'decisionWindowStart', 'decisionWindowEnd', 'end', 'playhead', 'reportedVideoTime', 'windowStart', 'windowEnd', 'audioAnchorTime'] as const) {
      const value = source[key];
      detail[key] = typeof value === 'number' && Number.isFinite(value) ? value : null;
    }
    for (const key of ['audioActive', 'nativeAudioReady', 'playing', 'prepared'] as const) {
      const value = source[key];
      detail[key] = typeof value === 'boolean' ? value : null;
    }
    window.dispatchEvent(new CustomEvent('cutvoke:preview-stop', { detail }));
  } catch { /* Even hostile getters/listeners must not change playback outcomes. */ }
}

export type AudioClockResetReason = 'mixer-disabled' | 'startup-begin' | 'startup-failed' | 'clock-fallback' | 'playhead-layout';
type AudioClockResetSnapshot = {
  playhead: number; reportedTime: number; anchor: number; playing: boolean; nativeReady: boolean; scrubbing: boolean;
};

/** Observe active clock resets only. Do not read the clock to diagnose why it reset. */
export function recordAudioClockReset(reason: AudioClockResetReason, snapshot: () => AudioClockResetSnapshot) {
  try {
    if ((window as unknown as Record<string, unknown>).__cutvokeAudioClockResetEvidence !== true) return;
    const source = snapshot();
    const detail = { reason, playhead: Number.isFinite(source.playhead) ? source.playhead : null,
      reportedTime: Number.isFinite(source.reportedTime) ? source.reportedTime : null,
      anchor: Number.isFinite(source.anchor) ? source.anchor : null, playing: source.playing,
      nativeReady: source.nativeReady, scrubbing: source.scrubbing };
    window.dispatchEvent(new CustomEvent('cutvoke:audio-clock-reset', { detail }));
  } catch { /* Diagnostics must not change playback. */ }
}
