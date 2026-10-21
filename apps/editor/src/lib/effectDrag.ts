import type { Clip, Track } from "../types/api";

export const EFFECT_DND_TYPE = "application/x-cutvoke-effect";

export interface EffectDragResource {
  effectId: string;
  category: string;
  appliesTo: string[];
  presetId?: string;
  duration?: number;
}

// HTML dragover cannot read getData(). Keep the in-app drag's metadata until
// dragend so incompatible clips can reject the cursor before the drop.
let active: EffectDragResource | null = null;
export function setEffectDrag(resource: EffectDragResource | null) { active = resource; }
export function getEffectDrag() { return active; }

export function clipAcceptsEffect(resource: Pick<EffectDragResource, "appliesTo">, track: Track, clip: Clip) {
  const path = clip.assetRef.sourcePath.split("?", 1)[0].toLowerCase();
  const types = track.kind === "audio" ? ["audio"] : track.kind === "text" ? ["text"]
    : /\.(avif|bmp|gif|heic|jpe?g|png|tiff?|webp)$/.test(path) ? ["image"] : ["video", "audio"];
  return resource.appliesTo.some(type => types.includes(type));
}
