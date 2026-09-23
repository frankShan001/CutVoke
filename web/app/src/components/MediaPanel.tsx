/** 素材库面板（左区）：文件导入 + 素材列表（会话资产 + 工程内源文件），可拖到时间线。
    导入：POST /api/v1/assets?name= → {assetId, path} → probe 时长 → 入素材库（独立资产库）。
    拖放协议：dataTransfer text/cutvoke-media = JSON {sourcePath, assetId?, kind}。 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Film, FileVideo, Music, Image as ImageIcon, Upload, Loader2, Search, Replace } from "lucide-react";
import { Panel, Button } from "./ui";
import { useEditor } from "../store/editor";
import { sourceBasename } from "../lib/media";
import { getSessionAssets, subscribeAssets, hydrateAssets } from "../lib/assetStore";
import { mediaFitsTrack, uploadFiles } from "../lib/importMedia";
import { listAssets, assetThumbnailUrl } from "../lib/mediaApi";
import { swapClipAsset } from "../store/clipEdit";

interface ClipAsset {
  sourcePath: string;
  name: string;
  kind: "video" | "audio" | "image" | "unknown";
  duration: number | null;
}

type KindFilter = "all" | "video" | "audio" | "image";
type SourceFilter = "mine" | "builtin";
const KIND_FILTERS: { value: KindFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "video", label: "视频" },
  { value: "audio", label: "音频" },
  { value: "image", label: "图片" },
];

export function MediaPanel() {
  const { state, dispatch } = useEditor();
  const editLocked = Boolean(state.editLock);
  const [dragging, setDragging] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [swappingPath, setSwappingPath] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("mine");
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
    const open = () => {
      if (editLocked) return;
      fileRef.current?.click();
    };
    window.addEventListener("cutvoke:open-import", open);
    return () => window.removeEventListener("cutvoke:open-import", open);
  }, [editLocked]);

  const openImport = () => {
    if (editLocked) return;
    fileRef.current?.click();
  };

  // 工程 clips 提取的源文件（复用已有素材）
  const clipAssets = useMemo<ClipAsset[]>(() => {
    const map = new Map<string, ClipAsset>();
    const tracks = state.project?.sequence.tracks || [];
    for (const t of tracks) {
      for (const c of t.clips || []) {
        const p = c.assetRef?.sourcePath;
        if (!p) continue;
        const kind = inferAssetKind(p, t.kind === "audio" ? "audio" : "video");
        if (!map.has(p)) {
          map.set(p, { sourcePath: p, name: sourceBasename(p), kind, duration: null });
        }
      }
    }
    return [...map.values()];
  }, [state.project]);

  const sessionAssets = getSessionAssets();
  const assets: { path: string; name: string; kind: ClipAsset["kind"]; duration: number | null; assetId?: string; builtIn: boolean }[] = useMemo(() => {
    // 会话资产优先；工程 clips 里未导入过的源文件也展示
    const seen = new Set(sessionAssets.map((a) => a.path));
    const extra = clipAssets
      .filter((a) => !seen.has(a.sourcePath))
      .map((a) => ({
        path: a.sourcePath,
        name: a.name,
        kind: a.kind,
        duration: a.duration,
        builtIn: isBuiltInAsset(a.name, a.sourcePath),
      }));
    return [
      ...sessionAssets.map((a) => ({
        path: a.path,
        name: a.name,
        kind: a.kind,
        duration: a.duration,
        assetId: a.assetId,
        builtIn: isBuiltInAsset(a.name, a.path),
      })),
      ...extra,
    ];
  }, [sessionAssets, clipAssets]);
  const mineCount = assets.filter((a) => !a.builtIn).length;
  const builtInCount = assets.length - mineCount;

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
        if (a.builtIn !== (sourceFilter === "builtin")) return false;
        if (q && !a.name.toLowerCase().includes(q)) return false;
        if (kindFilter !== "all" && a.kind !== kindFilter) return false;
        return true;
      });
  }, [assets, query, kindFilter, sourceFilter, usedRefs]);

  const handleFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    if (editLocked) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "Agent 正在编辑，素材导入已暂停" });
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    setImporting(true);
    try {
      const ok = await uploadFiles(
        Array.from(fileList),
        () => {},
        (name, message) => dispatch({ type: "STATUS_SET", severity: "warn", text: `导入 ${name} 失败：${message}` }),
      );
      if (ok > 0) dispatch({ type: "STATUS_SET", severity: "ok", text: `已导入 ${ok} 个素材` });
    } catch (error) {
      dispatch({
        type: "STATUS_SET",
        severity: "err",
        text: `素材导入失败：${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const startDrag = (e: React.DragEvent, a: { sourcePath: string; assetId?: string; kind: ClipAsset["kind"] }) => {
    if (editLocked) {
      e.preventDefault();
      return;
    }
    e.dataTransfer.setData("text/cutvoke-media", JSON.stringify({
      sourcePath: a.sourcePath,
      assetId: a.assetId,
      kind: a.kind,
    }));
    e.dataTransfer.effectAllowed = "copy";
    setDragging(a.sourcePath);
  };
  const endDrag = () => setDragging(null);

  // 选中片段 id（时间线选中才能换图套版）
  const selectedClipId = state.selection?.clipId;
  const selectedTrack = state.project?.sequence.tracks.find((track) => track.id === state.selection?.trackId);
  const selectedTrackLocked = Boolean(selectedTrack?.locked);

  const swapDisabledReason = (kind: ClipAsset["kind"]): string | null => {
    if (editLocked) return "Agent 正在编辑，暂不可替换素材";
    if (!selectedClipId || !selectedTrack) return "请先在时间线选中片段";
    if (selectedTrackLocked) return "所选轨道已锁定，解锁后才能替换素材";
    if (!mediaFitsTrack(kind, selectedTrack.kind)) {
      return selectedTrack.kind === "audio"
        ? "音频轨只能替换为音频素材"
        : "视频轨不能替换为音频素材";
    }
    return null;
  };

  // 换图套版：对当前选中片段调 asset.swap，仅替换素材引用，保留时长/动画/效果。
  const handleSwap = async (a: { path: string; assetId?: string; kind: ClipAsset["kind"] }) => {
    const blocked = swapDisabledReason(a.kind);
    if (blocked || !selectedClipId || swappingPath) return;
    setSwappingPath(a.path);
    try {
      await swapClipAsset(dispatch, state, {
        clipIds: [selectedClipId],
        sourcePath: a.path,
        assetId: a.assetId,
      });
    } finally {
      setSwappingPath(null);
    }
  };

  return (
    <Panel title="素材库" subtitle={`${mineCount} 个素材 · ${builtInCount} 个内置资源`}>
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <Button
          variant="primary"
          full
          onClick={openImport}
          disabled={importing || editLocked}
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
      <div className="media-sources" role="tablist" aria-label="素材来源">
        <button
          type="button"
          role="tab"
          aria-selected={sourceFilter === "mine"}
          className={sourceFilter === "mine" ? "media-source--active" : ""}
          onClick={() => setSourceFilter("mine")}
        >
          我的素材 <span>{mineCount}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={sourceFilter === "builtin"}
          className={sourceFilter === "builtin" ? "media-source--active" : ""}
          onClick={() => setSourceFilter("builtin")}
        >
          内置资源 <span>{builtInCount}</span>
        </button>
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
          <p className="cv-empty">
            {sourceFilter === "mine" && mineCount === 0
              ? "还没有导入自己的素材。内置背景和贴纸可在“内置资源”中查看。"
              : "没有匹配的素材。试试调整搜索词或类型筛选。"}
          </p>
        </div>
      ) : (
        <div className="media-panel">
          {filtered.map((a) => {
            const disabledReason = swapDisabledReason(a.kind);
            return (
            <div
              key={a.path + a.name}
              className={`media-item ${dragging === a.path ? "media-item--dragging" : ""}`}
              draggable={!editLocked}
              onDragStart={(e) => startDrag(e, { sourcePath: a.path, assetId: a.assetId, kind: a.kind })}
              onDragEnd={endDrag}
              title={`${a.name}\n${a.path}${disabledReason && selectedClipId ? `\n${disabledReason}` : ""}`}
            >
              {a.assetId && (a.kind === "image" || a.kind === "video") ? (
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
              <div className="media-item__details">
                <span className="media-item__name">{a.name}</span>
                <span className="media-item__meta">
                  {a.duration ? `${a.duration.toFixed(1)}s` : assetKindLabel(a.kind)}
                </span>
              </div>
              {a.used ? <span className="media-badge" aria-label="已用">已用</span> : null}
              <button
                type="button"
                className={`media-item__swap ${disabledReason ? "media-item__swap--disabled" : ""}`}
                disabled={Boolean(disabledReason) || swappingPath === a.path}
                aria-label={disabledReason || "替换选中片段"}
                title={disabledReason || "替换素材，保留片段的时长与效果"}
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
            );
          })}
        </div>
      )}
      <p className="cv-hint" style={{ marginTop: 8 }}>
        拖到轨道上即可插入，后续片段会顺延（时长识别失败按 2 秒）；拖到下方空白可自动建轨。
      </p>
    </Panel>
  );
}

function inferAssetKind(path: string, fallback: ClipAsset["kind"]): ClipAsset["kind"] {
  const cleanPath = path.split(/[?#]/, 1)[0].toLowerCase();
  const ext = cleanPath.split(".").pop() || "";
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif"].includes(ext)) return "image";
  if (["mp3", "wav", "flac", "aac", "m4a", "ogg", "opus"].includes(ext)) return "audio";
  if (["mp4", "mov", "m4v", "webm", "mkv", "avi", "mpeg", "mpg"].includes(ext)) return "video";
  return fallback;
}

function isBuiltInAsset(name: string, path: string): boolean {
  return name.startsWith("内置·") || /(?:^|[\\/])cutvoke[\\/]assets[\\/]/i.test(path);
}

function assetKindLabel(kind: string): string {
  if (kind === "image") return "图片";
  if (kind === "audio") return "音频";
  if (kind === "video") return "视频";
  return "未知类型";
}
