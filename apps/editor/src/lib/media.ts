/** 媒体路径辅助：从 sourcePath 提取源文件名（basename）。
    兼容 Windows（\）与 POSIX（/）路径分隔符。 */

export function sourceBasename(path: string | undefined | null): string {
  if (!path) return "—";
  const segs = path.split(/[\\/]/).filter(Boolean);
  return segs.length ? segs[segs.length - 1] : path;
}

/** 源路径截断显示（目录…/文件，控制宽度）。 */
export function sourcePathShort(path: string | undefined | null): string {
  if (!path) return "—";
  const name = sourceBasename(path);
  const idx = path.lastIndexOf(name);
  if (idx <= 0) return name;
  const dir = path.slice(0, idx);
  return `…${dir.slice(-24)}${name}`;
}