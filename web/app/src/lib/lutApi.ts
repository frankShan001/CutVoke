/** Import a 3D .cube LUT into CutVoke's managed local media directory. */

export interface ImportedLut {
  lutId: string;
  name: string;
  path: string;
  sha256: string;
  size: number;
  edgeSize: number;
  inputColorSpace: string;
}

export async function uploadLut(file: File): Promise<
  { kind: "ok"; data: ImportedLut } | { kind: "error"; message: string }
> {
  if (!file.name.toLowerCase().endsWith(".cube")) {
    return { kind: "error", message: "请选择 3D .cube LUT 文件" };
  }
  if (!file.size || file.size > 16 * 1024 * 1024) {
    return { kind: "error", message: "LUT 文件须在 1 字节到 16 MiB 之间" };
  }
  try {
    const query = new URLSearchParams({ name: file.name });
    const response = await fetch(`/api/v1/luts?${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    const result = await response.json() as ImportedLut | { error?: { message?: string } };
    if (!response.ok) {
      return { kind: "error", message: (result as { error?: { message?: string } }).error?.message
        || `导入失败（HTTP ${response.status}）` };
    }
    return { kind: "ok", data: result as ImportedLut };
  } catch (error) {
    return { kind: "error", message: error instanceof Error ? error.message : "LUT 上传失败" };
  }
}
