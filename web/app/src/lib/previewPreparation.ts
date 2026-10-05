import type { Project } from "../types/api";
import { ApiFailure } from "./api";

export function previewContentKey(project: Project | null): string {
  if (!project) return "";
  return JSON.stringify({
    sequence: { ...project.sequence, captions: [] },
    sequences: project.sequences?.map((sequence) => ({ ...sequence, captions: [] })),
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
    message: result.error?.message || "预览准备失败", retryable: true,
  });
  return result as T;
}
