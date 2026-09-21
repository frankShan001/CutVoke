/** 素材库面板（左区）：文件导入 + 素材列表（会话资产 + 工程内源文件），可拖到时间线。
    导入：POST /api/v1/assets?name= → {assetId, path} → probe 时长 → 入素材库（独立资产库）。
    拖放协议：dataTransfer text/cutvoke-media = JSON {sourcePath, assetId?}。 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Film, FileVideo, Music, Image as ImageIcon, Upload, Loader2, Search, Replace } from "lucide-react";
import { Panel, Button } from "./ui";
import { useEditor } from "../store/editor";
import { sourceBasename } from "../lib/media";
import { getSessionAssets, subscribeAssets, hydrateAssets } from "../lib/assetStore";
import { uploadFiles } from "../lib/importMedia";
import { listAssets, assetThumbnailUrl } from "../lib/mediaApi";
import { swapClipAsset } from "../store/clipEdit";

interface ClipAsset {
  sourcePath: string;
  name: string;
  kind: "video" | "audio" | "unknown";
  duration: number | null;
}

type KindFilter = "all" | "video" | "audio" | "image";
const KIND_FILTERS: { value: KindFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "video", label: "视频" },
  { value: "audio", label: "音频" },
  { value: "image", label: "图片" },
];

export function MediaPanel() {
  const { state, dispatch } = useEditor();
  const [dragging, setDragging] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [swappingPath, setSwappingPath] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [, force] = useState(0); // 监听会话资产变化
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => subscribeAssets(() => force((n) => n + 1)), []);

  // 挂载时拉取服务端素材库（D02：素材刷新不丢）。幂等：已存在会话内的项不会被覆盖。
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await listAssets();
      if (cancelled || res.kind !== "ok") return;
      hydrateAssets(
        res.data.map((a) => ({
          assetId: a.assetId,
          path: a.path,
          name: a.name,
          size: a.size,
          kind: a.kind,
          duration: a.duration,
        })),
      );
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 全局「打开文件导入」事件（时间线空态大按钮等触发）
  useEffect(() => {
    const open = () => fileRef.current?.click();
    window.addEventListener("cutvoke:open-import", open);
    return () => window.removeEventListener("cutvoke:open-import", open);
  }, []);

  const openImport = () => fileRef.current?.click();

  // 工程 clips 提取的源文件（复用已有素材）
  const clipAssets = useMemo<ClipAsset[]>(() => {
    const map = new Map<string, ClipAsset>();
    const tracks = state.project?.sequence.tracks || [];
    for (const t of tracks) {
      for (const c of t.clips || []) {
        const p = c.assetRef?.sourcePath;
        if (!p) continue;
        const kind = t.kind === "audio" ? "audio" : "video";
        if (!map.has(p)) {
          map.set(p, { sourcePath: p, name: sourceBasename(p), kind, duration: null });
        }
      }
    }
    return [...map.values()];
  }, [state.project]);

  const sessionAssets = getSessionAssets();
  const assets: { path: string; name: string; kind: string; duration: number | null; assetId?: string }[] = useMemo(() => {
    // 会话资产优先；工程 clips 里未导入过的源文件也展示
    const seen = new Set(sessionAssets.map((a) => a.path));
    const extra = clipAssets
      .filter((a) => !seen.has(a.sourcePath))
      .map((a) => ({ path: a.sourcePath, name: a.name, kind: a.kind, duration: a.duration }));
    return [
      ...sessionAssets.map((a) => ({ path: a.path, name: a.name, kind: a.kind, duration: a.duration, assetId: a.assetId })),
      ...extra,
    ];
  }, [sessionAssets, clipAssets]);

  // 已用判定：素材的 path / assetId 出现在任意轨道 clip 的 assetRef 中（D02）。
  const usedRefs = useMemo(() => {
    const paths = new Set<string>();
    const ids = new Set<string>();
    for (const t of state.project?.sequence.tracks || []) {
      for (const c of t.clips || []) {
        if (c.assetRef?.sourcePath) paths.add(c.assetRef.sourcePath);
        if (c.assetRef?.assetId) ids.add(c.assetRef.assetId);
      }
    }
    return { paths, ids };
  }, [state.project]);

  // 客户端即时过滤：按 name 子串（不区分大小写）+ kind 类型过滤（D02）。
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return assets
      .map((a) => ({
        ...a,
        used: usedRefs.paths.has(a.path) || (a.assetId ? usedRefs.ids.has(a.assetId) : false),
      }))
      .filter((a) => {
        if (q && !a.name.toLowerCase().includes(q)) return false;
        if (kindFilter !== "all" && a.kind !== kindFilter) return false;
        return true;
      });
  }, [assets, query, kindFilter, usedRefs]);

  const handleFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setImporting(true);
    const ok = await uploadFiles(
      Array.from(fileList),
      () => {},
      (name, message) => dispatch({ type: "STATUS_SET", severity: "warn", text: `导入 ${name} 失败：${message}` }),
    );
    setImporting(false);
    if (ok > 0) dispatch({ type: "STATUS_SET", severity: "ok", text: `已导入 ${ok} 个素材` });
    if (fileRef.current) fileRef.current.value = "";
  };

  const startDrag = (e: React.DragEvent, a: { sourcePath: string; assetId?: string }) => {
    e.dataTransfer.setData("text/cutvoke-media", JSON.stringify({ sourcePath: a.sourcePath, assetId: a.assetId }));
    e.dataTransfer.effectAllowed = "copy";
    setDragging(a.sourcePath);
  };
  const endDrag = () => setDragging(null);

  // 选中片段 id（时间线选中才能换图套版）
  const selectedClipId = state.selection?.clipId;

  // 换图套版：对当前选中片段调 asset.swap，仅替换素材引用，保留时长/动画/效果。
  const handleSwap = async (a: { path: string; assetId?: string }) => {
    if (!selectedClipId || swappingPath) return;
    setSwappingPath(a.path);
    await swapClipAsset(dispatch, state, {
      clipIds: [selectedClipId],
      sourcePath: a.path,
      assetId: a.assetId,
    });
    setSwappingPath(null);
  };

  return (
    <Panel title="我的素材" subtitle={`${assets.length} 个文件`}>
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <Button
          variant="primary"
          full
          onClick={openImport}
          disabled={importing}
          style={{ height: 30 }}
          aria-label="导入素材"
        >
          {importing ? <Loader2 size={14} className="cv-spin" /> : <Upload size={14} />}
          {importing ? "导入中…" : "导入文件"}
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept="video/*,audio/*,image/*"
          multiple
          style={{ display: "none" }}
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>
      <div className="media-filter">
        <label className="media-filter__search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索素材名…"
            aria-label="搜索素材"
          />
        </label>
        <div className="media-filter__kinds" role="group" aria-label="素材类型过滤">
          {KIND_FILTERS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className={`media-filter__kind ${kindFilter === opt.value ? "media-filter__kind--active" : ""}`}
              aria-pressed={kindFilter === opt.value}
              onClick={() => setKindFilter(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      {assets.length === 0 ? (
        <div className="media-empty">
          <p className="cv-empty">素材库为空。点击上方「导入文件」上传视频/音频/图片，即可拖到时间线。</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="media-empty">
          <p className="cv-empty">没有匹配的素材。试试调整搜索词或类型筛选。</p>
        </div>
      ) : (
        <div className="media-panel">
          {filtered.map((a) => (
            <div
              key={a.path + a.name}
              className={`media-item ${dragging === a.path ? "media-item--dragging" : ""}`}
              draggable
              onDragStart={(e) => startDrag(e, { sourcePath: a.path, assetId: a.assetId })}
              onDragEnd={endDrag}
              title={a.path}
            >
              {a.assetId ? (
                <img
                  className="media-item__thumb"
                  src={assetThumbnailUrl(a.assetId)}
                  alt=""
                  loading="lazy"
                  onError={(e) => {
                    (e.currentTarget as HTMLImageElement).style.display = "none";
                  }}
                />
              ) : a.kind === "image" ? (
                <ImageIcon size={14} className="cv-ic--video" />
              ) : a.kind === "audio" ? (
                <Music size={14} className="cv-ic--audio" />
              ) : a.kind === "video" ? (
                <FileVideo size={14} className="cv-ic--video" />
              ) : (
                <Film size={14} className="cv-ic--video" />
              )}
              <span className="media-item__name">{a.name}</span>
              <span className="media-item__meta">
                {a.duration ? `${a.duration.toFixed(1)}s` : a.kind}
              </span>
              {a.used ? (
                <span className="media-badge" aria-label="已用">已用</span>
              ) : (
                <span className="media-badge media-badge--unused" aria-label="未用">未用</span>
              )}
              <button
                type="button"
                className={`media-item__swap ${!selectedClipId ? "media-item__swap--disabled" : ""}`}
                disabled={!selectedClipId || swappingPath === a.path}
                aria-label="替换选中片段"
                title={
                  selectedClipId
                    ? "换图不换布局：保留时长/动画/效果，仅替换素材画面"
                    : "请先在时间线选中片段"
                }
                onMouseDown={(e) => e.stopPropagation()}
                onClick={() => handleSwap(a)}
              >
                {swappingPath === a.path ? (
                  <Loader2 size={14} className="cv-spin" />
                ) : (
                  <Replace size={14} />
                )}
              </button>
            </div>
          ))}
        </div>
      )}
      <p className="cv-hint" style={{ marginTop: 8 }}>
        拖拽素材到时间线某条轨道即可导入（自动探测时长，失败按 2s）。
      </p>
    </Panel>
  );
}