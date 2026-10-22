/** Transient previews and atomic gesture commits, shared by every inspector. */
import { useMemo, useSyncExternalStore, type Dispatch } from "react";
import type { Clip, Project } from "../types/api";
import type { EditorAction } from "./editor";
import { getLatestState, runCommand } from "./actions";
import { registerPendingEdit } from "../lib/pendingEdits";
import { evaluateKeyframes } from "../lib/keyframes";
import { rationalToSecs, secsToRational } from "../lib/rational";

export type Edit = { type: string; payload: Record<string, unknown> };
type Session = { projectId: string; edits: Edit[]; held: boolean; generation: number;
  timer?: number; saving?: Promise<boolean>; unregister: () => void; dispatch: Dispatch<EditorAction>; error: boolean; pointerHeld?: boolean };
const sessions = new Map<string, Session>();
const listeners = new Set<() => void>();
let version = 0;
let serial: Promise<unknown> = Promise.resolve();
let pointerDown = false;
if (typeof document !== "undefined") {
  document.addEventListener("pointerdown", () => { pointerDown = true; }, true);
  const release = () => {
    pointerDown = false;
    queueMicrotask(() => { for (const [key, session] of sessions) if (session.pointerHeld) { session.pointerHeld = false; session.held = false; void saveSession(key); } });
  };
  document.addEventListener("pointerup", release, true);
  document.addEventListener("pointercancel", release, true);
}
const notify = () => { version++; for (const listener of listeners) listener(); };
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const useInteractionVersion = () => useSyncExternalStore(subscribe, () => version, () => 0);

export function interactionStatus(projectId: string) {
  const active = [...sessions.values()].filter(s => s.projectId === projectId);
  return { pending: active.length > 0, failed: active.some(s => s.error) };
}

export function pendingCommands(projectId: string): Edit[] {
  return [...sessions.values()].filter(s => s.projectId === projectId).flatMap(s => s.edits);
}

export function stageEdit(dispatch: Dispatch<EditorAction>, key: string, edits: Edit[], held = false) {
  const state = getLatestState();
  if (!state?.currentId || state.editLock || !edits.length) return;
  key = `${state.currentId}:${key}`;
  let session = sessions.get(key);
  if (!session) {
    session = { projectId: state.currentId, edits: [], held, generation: 0, dispatch, error: false, unregister: () => {} };
    sessions.set(key, session);
    session.unregister = registerPendingEdit(key, () => saveSession(key));
  }
  clearTimeout(session.timer);
  session.edits = edits;
  session.pointerHeld = pointerDown && !held;
  session.held = held || pointerDown;
  session.generation++;
  session.error = false;
  notify();
  if (!session.held) session.timer = window.setTimeout(() => void saveSession(key), 400);
}

export function releaseEdit(key: string) {
  const pid = getLatestState()?.currentId;
  const session = sessions.get(`${pid}:${key}`);
  if (!session) return;
  session.held = false;
  void saveSession(`${pid}:${key}`);
}

export function cancelInteractions(projectId: string) {
  for (const [key, session] of sessions) if (session.projectId === projectId) {
    clearTimeout(session.timer); session.unregister(); sessions.delete(key);
  }
  notify();
}

async function saveSession(key: string): Promise<boolean> {
  const session = sessions.get(key);
  if (!session) return true;
  clearTimeout(session.timer);
  if (session.saving) {
    if (!await session.saving) return false;
    return sessions.has(key) ? saveSession(key) : true;
  }
  const generation = session.generation;
  const edits = structuredClone(session.edits);
  const save = async () => {
    const state = getLatestState();
    if (sessions.get(key) !== session) return true;
    if (state?.currentId !== session.projectId || state.editLock) return false;
    for (const edit of edits) {
      if (edit.type === "effect.add" && state.project?.sequence.tracks.flatMap(t => t.clips).find(c => c.id === edit.payload.clipId)?.effects?.some(e => e.effectId === edit.payload.effectId)) edit.type = "effect.update";
    }
    const result = await runCommand(session.dispatch, state,
      edits.length === 1 ? edits[0].type : "edit.batch",
      edits.length === 1 ? edits[0].payload : { commands: edits });
    if (!result.ok) { session.error = true; notify(); return false; }
    if (sessions.get(key) === session && generation === session.generation) {
      session.unregister(); sessions.delete(key); notify();
    }
    return true;
  };
  session.saving = serial.then(save, save).finally(() => { session.saving = undefined; });
  serial = session.saving;
  return session.saving;
}

function patchClip(project: Project, id: unknown, edit: Edit) {
  const clip = project.sequence.tracks.flatMap(track => track.clips).find(item => item.id === id);
  if (!clip) return;
  const p = edit.payload;
  if (edit.type === "clip.audio") {
    for (const name of ["volume", "fadeIn", "fadeOut"] as const)
      if (p[name] !== undefined) clip[name] = typeof p[name] === "number" ? secsToRational(p[name]) : p[name] as Clip[typeof name];
  } else if (edit.type === "clip.keyframe") {
    const lanes = clip.keyframes ||= {};
    const frames = p.keyframes as { param: string; time: { num: string; den: string }; value: number; interpolation?: "linear" }[];
    for (const frame of frames || []) {
      const lane = lanes[frame.param] ||= [];
      const time = rationalToSecs(frame.time);
      const existing = lane.find(item => Math.abs(rationalToSecs(item.time) - time) < 1e-6);
      if (existing) existing.value = frame.value;
      else lane.push({ id: "draft", time: frame.time, value: frame.value, interpolation: frame.interpolation || "linear" });
    }
  } else if (edit.type.startsWith("effect.")) {
    const effects = clip.effects ||= [];
    const index = typeof p.effectIndex === "number" ? p.effectIndex : effects.findIndex(effect => effect.effectId === p.effectId);
    const effect = effects[index];
    if (edit.type === "effect.add" && effect) effect.params = { ...effect.params, ...p.params as object };
    else if (edit.type === "effect.add" && !effect) effects.push({ effectId: String(p.effectId), version: "1.0.0", params: p.params as Record<string, unknown> });
    else if (effect && edit.type === "effect.update") effect.params = { ...effect.params, ...p.params as object };
    else if (effect && edit.type === "effect.bypass") effect.enabled = Boolean(p.enabled);
  }
}

export function interactionProject(project: Project | null): Project | null {
  if (!project) return project;
  const edits = pendingCommands(project.projectId);
  if (!edits.length) return project;
  const next = structuredClone(project);
  for (const edit of edits) {
    if (edit.type === "caption.update") {
      const caption = next.sequence.captions?.find(item => item.id === edit.payload.captionId);
      if (caption) Object.assign(caption, Object.fromEntries(Object.entries(edit.payload).filter(([key]) => key !== "captionId")));
    } else patchClip(next, edit.payload.clipId, edit);
  }
  return next;
}

export function useInteractionProject(project: Project | null) {
  const tick = useInteractionVersion();
  return useMemo(() => interactionProject(project), [project, tick]);
}

export function stageEffect(dispatch: Dispatch<EditorAction>, clip: Clip, effectId: string,
  params: Record<string, unknown>, held = false, key = `${clip.id}:${effectId}`) {
  const exists = clip.effects?.some(effect => effect.effectId === effectId);
  stageEdit(dispatch, key, [{ type: exists ? "effect.update" : "effect.add", payload: { clipId: clip.id, effectId, params } }], held);
}

export function stageCaption(dispatch: Dispatch<EditorAction>, captionId: string, patch: Record<string, unknown>, held = false) {
  const key = `caption:${captionId}`, pid = getLatestState()?.currentId;
  const previous = sessions.get(`${pid}:${key}`)?.edits[0]?.payload || {};
  stageEdit(dispatch, key, [{ type: "caption.update", payload: { ...previous, captionId, ...patch } }], held);
}

/** Preserve animated lanes and commit mixed base/keyframe changes together. */
export function stageTransform(dispatch: Dispatch<EditorAction>, clip: Clip, values: Record<string, number>, localTime: number, held = false) {
  clip = getLatestState()?.project?.sequence.tracks.flatMap(t => t.clips).find(c => c.id === clip.id) || clip;
  const params: Record<string, unknown> = {};
  const keyframes: { param: string; time: ReturnType<typeof secsToRational>; value: number; interpolation: "linear" }[] = [];
  const current = clip.effects?.find(e => e.effectId === "cutvoke.transform")?.params || {};
  const position = { ...(current.position as { x?: number; y?: number } || {}) };
  let moved = false;
  for (const [param, raw] of Object.entries(values)) {
    const value = param === "rotation" ? ((raw + 540) % 360) - 180 : raw;
    const base = param === "x" || param === "y" ? Number(position[param] ?? 0) : Number(current[param] ?? (param === "rotation" ? 0 : 1));
    if (Math.abs(evaluateKeyframes(clip.keyframes?.[param], localTime, base) - value) < 1e-6) continue;
    if (clip.keyframes?.[param]?.length) keyframes.push({ param, time: secsToRational(Math.max(0, localTime)), value, interpolation: "linear" });
    else if (param === "x" || param === "y") { position[param] = Math.round(value); moved = true; }
    else params[param] = value;
  }
  if (moved) params.position = { x: position.x ?? 0, y: position.y ?? 0 };
  const edits: Edit[] = [];
  if (Object.keys(params).length) edits.push({ type: clip.effects?.some(e => e.effectId === "cutvoke.transform") ? "effect.update" : "effect.add", payload: { clipId: clip.id, effectId: "cutvoke.transform", params } });
  if (keyframes.length) edits.push({ type: "clip.keyframe", payload: { clipId: clip.id, action: "batch", keyframes } });
  const key = `${clip.id}:cutvoke.transform`;
  if (!edits.length) {
    const fullKey = `${getLatestState()?.currentId}:${key}`, session = sessions.get(fullKey);
    if (session && !session.saving) { clearTimeout(session.timer); session.unregister(); sessions.delete(fullKey); notify(); }
  } else stageEdit(dispatch, key, edits, held);
}
