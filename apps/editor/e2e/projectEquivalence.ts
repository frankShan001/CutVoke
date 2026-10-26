export type Project = {
  projectId: string;
  revision: string;
  sequence: {
    width: number;
    height: number;
    fps: { num: string; den: string };
    tracks: Array<{
      id: string;
      kind: string;
      role?: string;
      visible?: boolean;
      muted?: boolean;
      locked?: boolean;
      clips: Array<{
        id: string;
        assetRef: { sourcePath: string; assetId?: string; resourceRef?: Record<string, unknown> };
        role?: "sticker";
        timelineStart: { num: string; den: string };
        timelineEnd: { num: string; den: string };
        sourceStart?: { num: string; den: string };
        speed?: { num: string; den: string };
        attachedToClipId?: string | null;
        keyframes?: Record<string, Array<{
          id: string; time: { num: string; den: string }; value: unknown; interpolation?: string;
        }>>;
        effects: Array<{
          effectId: string; instanceId?: string; version?: string; params?: Record<string, unknown>;
          presetId?: string; range?: { start: { num: string; den: string }; end: { num: string; den: string } };
        }>;
      }>;
    }>;
    captions: Array<{
      id: string; text: string; start: { num: string; den: string }; end: { num: string; den: string };
      [key: string]: unknown;
    }>;
  };
};


/** Instance IDs are fresh in independently executed commands. Compare the
 * reference graph, retaining effect order, every parameter and keyframe. */
function canonicalEffects(effects: Array<{ instanceId?: string }>, keyframes: Record<string, unknown>) {
  const ids = new Map<string, string>();
  const normalized = effects.map((effect, index) => {
    const id = effect.instanceId;
    if (typeof id !== "string") return effect;
    if (ids.has(id)) throw new Error(`Duplicate effect instance: ${id}`);
    const canonical = `instance_${index}`;
    ids.set(id, canonical);
    return { ...effect, instanceId: canonical };
  });
  const tracks = Object.fromEntries(Object.entries(keyframes).map(([key, value]) => {
    if (!key.startsWith("effect:")) return [key, value];
    const [, id, parameter] = key.split(":");
    if (!ids.has(id)) throw new Error(`Unresolved effect keyframe: ${key}`);
    return [`effect:${ids.get(id)}:${parameter}`, value];
  }));
  return { effects: normalized, keyframes: tracks };
}

export function structuralSignature(project: Pick<Project, "sequence">): string {
  const shape = {
    width: project.sequence.width,
    height: project.sequence.height,
    fps: project.sequence.fps,
    tracks: project.sequence.tracks.map((track) => ({
      id: track.id,
      kind: track.kind,
      role: track.role ?? "",
      visible: track.visible ?? true,
      muted: track.muted ?? false,
      locked: track.locked ?? false,
      clips: track.clips.map((clip) => ({
        id: clip.id,
        assetRef: clip.assetRef,
        role: clip.role ?? "",
        timelineStart: clip.timelineStart,
        timelineEnd: clip.timelineEnd,
        sourceStart: clip.sourceStart,
        speed: clip.speed,
        attachedToClipId: clip.attachedToClipId ?? null,
        ...canonicalEffects(clip.effects || [], clip.keyframes || {}),
      })),
    })),
    captions: project.sequence.captions,
  };
  const canonicalize = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonicalize);
    if (typeof value === "object" && value !== null) {
      return Object.fromEntries(Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]));
    }
    return value;
  };
  return JSON.stringify(canonicalize(shape));
}

