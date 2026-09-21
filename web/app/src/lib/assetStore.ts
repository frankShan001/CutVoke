/** 会话级资产库（WP-03/A07）。
    仅持有 assetId 引用 + 会话内 path；不写 localStorage（绝对路径不持久化）。
    资产即使未进入时间线也会出现在素材库。 */

import { sourceBasename } from "./media";

export interface SessionAsset {
  assetId: string;
  path: string;
  name: string;
  size: number;
  kind: "video" | "audio" | "image" | "unknown";
  duration: number | null;
}

/** 根据扩展名/探测结果推断类型。 */
export function inferKind(name: string, hasVideo: boolean, hasAudio: boolean): SessionAsset["kind"] {
  const lower = name.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|bmp)/.test(lower)) return "image";
  if (hasVideo) return "video";
  if (hasAudio) return "audio";
  if (/\.(mp3|wav|flac|aac|m4a|ogg)/.test(lower)) return "audio";
  return "unknown";
}

let assets: SessionAsset[] = [];
const listeners = new Set<() => void>();

export function getSessionAssets(): SessionAsset[] {
  return assets;
}

export function addSessionAsset(a: SessionAsset): void {
  assets = [...assets, a];
  emit();
}

/**
 * 用服务端资产账本填充会话素材库（挂载时调用一次，幂等）。
 * 按 path 去重：已存在于会话库的项（如本次会话刚导入的）不被覆盖，避免丢失
 * 本次会话内的最新探测信息。
 */
export function hydrateAssets(serverAssets: SessionAsset[]): void {
  const existing = new Set(assets.map((a) => a.path));
  const incoming = serverAssets.filter((a) => !existing.has(a.path));
  if (incoming.length === 0) return;
  assets = [...incoming, ...assets];
  emit();
}

export function clearSessionAssets(): void {
  assets = [];
  emit();
}

export function subscribeAssets(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit(): void {
  for (const fn of listeners) fn();
}

/** 由导入的 asset 构建会话素材（name 取 basename）。 */
export function sessionAssetFrom(
  imported: { assetId: string; path: string; size: number; name: string },
  kind: SessionAsset["kind"],
  duration: number | null,
): SessionAsset {
  return {
    assetId: imported.assetId,
    path: imported.path,
    name: imported.name || sourceBasename(imported.path),
    size: imported.size,
    kind,
    duration,
  };
}