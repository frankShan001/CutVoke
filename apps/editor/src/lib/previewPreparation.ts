import type { Sequence } from "../types/api";

function baseSequence(sequence: Sequence) {
  if (hasLayerGraph(sequence)) return { ...sequence, captions: [] };
  return { ...sequence, captions: [], tracks: sequence.tracks.map(track => track.kind !== "text" ? track :
    { ...track, clips: track.clips.map(clip => ({ ...clip, keyframes: {},
      effects: [{ effectId: "cutvoke.text", params: { content: "" } }] })) }) };
}

export function hasLayerGraph(sequence: Sequence): boolean {
  return sequence.tracks.some(track => track.clips.some(clip => !!clip.compositing && Object.keys(clip.compositing).length > 0 || clip.role === "adjustment"));
}

import { tr } from "../i18n";
import type { Project } from "../types/api";
import { ApiFailure } from "./api";

export function previewContentKey(project: Project | null): string {
  if (!project) return "";
  return JSON.stringify({
    sequence: baseSequence(project.sequence),
    sequences: project.sequences?.map((sequence) => sequence.id === project.sequence.id ? baseSequence(sequence) : ({ ...sequence, captions: [] })),
  });
}

export interface PreparedPreview {
  projectId: string;
  contentKey: string;
  url: string;
}

export interface PreviewJob {
  jobId: string;
  state: "queued" | "running" | "completed" | "failed" | "cancelled";
  phase: string;
  completedWindows: number;
  totalWindows: number;
  progress: number;
  cached: boolean;
  currentWindow?: number;
  windowStart?: number;
  windowEnd?: number;
  elapsedSeconds?: number;
  error?: string;
}

export async function previewRequest<T>(path: string, signal?: AbortSignal, body?: object): Promise<T> {
  const response = await fetch(`/api/v1/${path}`, {
    method: body === undefined ? "GET" : "POST", signal,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new ApiFailure({
    status: response.status, code: result.error?.code || "PREVIEW_FAILED",
    message: result.error?.message || tr("预览准备失败"), retryable: true,
  });
  return result as T;
}
