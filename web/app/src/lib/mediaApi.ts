/** 媒体相关 API：单帧预览 / 素材探测 / 文件导入资产。
    全部走同源相对路径，与后端 httpapi.py 对齐。 */

import type { ApiErrorBody } from "../types/api";

/** 素材探测结果（render.probe_media 返回）。 */
export interface ProbeResult {
  duration: number;
  width: number;
  height: number;
  has_video: boolean;
  has_audio: boolean;
  format: string;
}

export type ProbeOutcome = { kind: "ok"; data: ProbeResult } | { kind: "error"; message: string };

/**
 * 探测素材元信息 GET /api/v1/probe?path=。
 * 200 → 元信息；400/422 → PROBE_FAILED 等。响应包一层便于调用方直接使用。
 */
export async function probeMedia(path: string): Promise<ProbeOutcome> {
  const q = new URLSearchParams({ path });
  try {
    const res = await fetch(`/api/v1/probe?${q.toString()}`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    const data = await readJson(res);
    if (!res.ok) {
      const err = (data as { error?: ApiErrorBody } | undefined)?.error;
      return {
        kind: "error",
        message: err?.message || `探测失败（HTTP ${res.status}）`,
      };
    }
    return { kind: "ok", data: data as ProbeResult };
  } catch (err) {
    return { kind: "error", message: err instanceof Error ? err.message : "探测网络错误" };
  }
}

/** 导入资产结果（POST /api/v1/assets）。 */
export interface ImportedAsset {
  assetId: string;
  path: string;
  size: number;
  name: string;
}

/** 服务端资产账本里的素材富描述（GET /api/v1/assets）。 */
export interface ServerAsset {
  assetId: string;
  name: string;
  path: string;
  size: number;
  kind: "video" | "audio" | "image" | "unknown";
  duration: number | null;
  hasVideo: boolean;
  hasAudio: boolean;
  width: number | null;
  height: number | null;
  createdAt: string;
}

export type ListAssetsOutcome =
  | { kind: "ok"; data: ServerAsset[] }
  | { kind: "error"; message: string };

/**
 * 拉取服务端资产账本 GET /api/v1/assets。
 * 200 → { assets: [...] }；无持久化 store 时 404 → 视为空库（不报错，前端退化为会话内）。
 */
export async function listAssets(): Promise<ListAssetsOutcome> {
  try {
    const res = await fetch("/api/v1/assets", {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (res.status === 404) return { kind: "ok", data: [] };
    const data = await readJson(res);
    if (!res.ok) {
      const err = (data as { error?: ApiErrorBody } | undefined)?.error;
      return {
        kind: "error",
        message: err?.message || `读取素材库失败（HTTP ${res.status}）`,
      };
    }
    const items = (data as { assets?: ServerAsset[] } | undefined)?.assets || [];
    return { kind: "ok", data: items };
  } catch (err) {
    return { kind: "error", message: err instanceof Error ? err.message : "素材库网络错误" };
  }
}

export type ImportOutcome = { kind: "ok"; data: ImportedAsset } | { kind: "error"; message: string };

/** 素材缩略图 URL（D02）：GET /api/v1/assets/{id}/thumbnail?w=。 */
export function assetThumbnailUrl(assetId: string, w = 320): string {
  return `/api/v1/assets/${encodeURIComponent(assetId)}/thumbnail?w=${w}`;
}

/**
 * 上传本地文件到服务端资产库（WP-03/A07）。
 * POST /api/v1/assets?name=<filename>，body = 文件原始字节 → 201 {assetId, path, size, name}。
 * path 为服务端落盘绝对路径（前端不持久化绝对路径，仅会话内引用 assetId/path）。
 */
export async function uploadAsset(file: File): Promise<ImportOutcome> {
  const q = new URLSearchParams({ name: file.name });
  try {
    const res = await fetch(`/api/v1/assets?${q.toString()}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    const data = await readJson(res);
    if (!res.ok) {
      const err = (data as { error?: ApiErrorBody } | undefined)?.error;
      return {
        kind: "error",
        message: err?.message || `导入失败（HTTP ${res.status}）`,
      };
    }
    return { kind: "ok", data: data as ImportedAsset };
  } catch (err) {
    return { kind: "error", message: err instanceof Error ? err.message : "导入网络错误" };
  }
}

export interface PreviewFrameSpec {
  projectId: string;
  t: number;
  width?: number;
  height?: number;
}

export type PreviewFrameResult =
  | { kind: "frame"; url: string }
  | { kind: "empty" }
  | { kind: "error"; message: string };

/**
 * 获取单帧预览 PNG。成功时返回可 <img src> 的对象 URL（调用方负责 revoke）。
 * - 200 → PNG 二进制（blob URL）；204 → 黑帧；其它 → 结构化错误
 */
export async function fetchPreviewFrame(spec: PreviewFrameSpec): Promise<PreviewFrameResult> {
  const q = new URLSearchParams();
  q.set("t", String(spec.t));
  if (spec.width && spec.height) q.set("size", `${spec.width}x${spec.height}`);
  const url = `/api/v1/projects/${encodeURIComponent(spec.projectId)}/preview-frame?${q.toString()}`;
  try {
    const res = await fetch(url, { method: "GET", headers: { Accept: "image/png" } });
    if (res.status === 204) return { kind: "empty" };
    if (!res.ok) {
      let message = `预览帧失败（HTTP ${res.status}）`;
      try {
        const data = (await res.json()) as { error?: ApiErrorBody };
        if (data.error?.message) message = data.error.message;
      } catch {
        /* keep default */
      }
      return { kind: "error", message };
    }
    const blob = await res.blob();
    const objectUrl = URL.createObjectURL(blob);
    return { kind: "frame", url: objectUrl };
  } catch (err) {
    return { kind: "error", message: err instanceof Error ? err.message : "预览帧网络错误" };
  }
}

async function readJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return undefined;
  }
}