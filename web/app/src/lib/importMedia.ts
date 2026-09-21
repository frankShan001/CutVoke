/** 文件导入公共流程（WP-03）：上传 → 探测 → 入会话素材库。
    供 MediaPanel 的文件选择与时间线的「OS 文件拖入」共用，避免逻辑分叉。 */

import { uploadAsset, probeMedia } from "./mediaApi";
import { addSessionAsset, inferKind, type SessionAsset } from "./assetStore";

export interface UploadedMedia {
  assetId: string;
  path: string;
  name: string;
  size: number;
  kind: SessionAsset["kind"];
  duration: number | null;
}

export type UploadOneResult =
  | { ok: true; media: UploadedMedia }
  | { ok: false; name: string; message: string };

/** 上传单个文件：POST /assets → probe → 写入会话素材库。 */
export async function uploadOneFile(file: File): Promise<UploadOneResult> {
  const up = await uploadAsset(file);
  if (up.kind !== "ok") return { ok: false, name: file.name, message: up.message };
  const probe = await probeMedia(up.data.path);
  let dur: number | null = null;
  let kind: SessionAsset["kind"] = "unknown";
  if (probe.kind === "ok") {
    dur = probe.data.duration > 0 ? Math.round(probe.data.duration * 10) / 10 : null;
    kind = inferKind(up.data.name, probe.data.has_video, probe.data.has_audio);
  } else {
    kind = inferKind(up.data.name, false, false);
  }
  const media: UploadedMedia = {
    assetId: up.data.assetId,
    path: up.data.path,
    name: up.data.name,
    size: up.data.size,
    kind,
    duration: dur,
  };
  addSessionAsset({ ...media });
  return { ok: true, media };
}

/**
 * 上传一组文件；每个成功后回调 onEach(media, index) 做后续动作（如建轨插入）。
 * 返回成功上传数。失败的通过 onError 上报，不中断其余文件。
 */
export async function uploadFiles(
  files: File[],
  onEach: (media: UploadedMedia, index: number) => unknown,
  onError?: (name: string, message: string) => void,
): Promise<number> {
  let ok = 0;
  let index = 0;
  for (const f of files) {
    const res = await uploadOneFile(f);
    if (!res.ok) {
      onError?.(res.name, res.message);
      continue;
    }
    await onEach(res.media, index++);
    ok++;
  }
  return ok;
}
