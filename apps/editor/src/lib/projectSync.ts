import type { Project, Sequence } from "../types/api";

export type CompactProject = Omit<Project, "sequence"> & { sequence?: Sequence; activeSequenceId?: string };

/** Keep the legacy view as an alias, rather than a second decoded active sequence. */
export function expandProject(raw: CompactProject): Project {
  const sequence = raw.activeSequenceId ? raw.sequences?.find(item => item.id === raw.activeSequenceId) : raw.sequence;
  if (!sequence) throw new Error("Missing active sequence in project response");
  return { ...raw, sequence };
}

/** Unchanged clips/tracks retain React identities after a changed project sync. */
export function reuseProjectStructure(previous: Project | undefined, next: Project): Project {
  if (!previous || previous.projectId !== next.projectId) return next;
  const priorSequences = new Map((previous.sequences || [previous.sequence]).map(item => [item.id, item]));
  function sequence(current: Sequence): Sequence {
    const old = priorSequences.get(current.id);
    if (!old) return current;
    const tracks = new Map(old.tracks.map(track => [track.id, track]));
    const result = { ...current, tracks: current.tracks.map(track => {
      const prior = tracks.get(track.id);
      if (!prior) return track;
      const clips = new Map(prior.clips.map(clip => [clip.id, clip]));
      const reconciled = { ...track, clips: track.clips.map(clip => {
        const oldClip = clips.get(clip.id);
        return oldClip && JSON.stringify(oldClip) === JSON.stringify(clip) ? oldClip : clip;
      }) };
      return JSON.stringify(prior) === JSON.stringify(reconciled) ? prior : reconciled;
    }) };
    return JSON.stringify(old) === JSON.stringify(result) ? old : result;
  }
  const sequences = next.sequences?.map(sequence);
  const active = sequences?.find(item => item.id === next.sequence.id) || sequence(next.sequence);
  return { ...next, sequences, sequence: active };
}
