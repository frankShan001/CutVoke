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
