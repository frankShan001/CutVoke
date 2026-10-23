import { msg, tr, useLocale } from "../i18n";
import { readingOrderKey } from "../i18n/navigation";
/** 素材库面板（左区）：文件导入 + 素材列表（会话资产 + 工程内源文件），可拖到时间线。
    导入：POST /api/v1/assets?name= → {assetId, path} → probe 时长 → 入素材库（独立资产库）。
    拖放协议：dataTransfer text/cutvoke-media = JSON {sourcePath, assetId?, kind}。 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Film, FileVideo, Music, Image as ImageIcon, Upload, Loader2, Search, Replace, Play, Pause, Star } from "lucide-react";
import { Panel, Button } from "./ui";
import { useEditor } from "../store/editor";
import { sourceBasename } from "../lib/media";
import { getSessionAssets, subscribeAssets, hydrateAssets, updateSessionAssetAudioRole } from "../lib/assetStore";
import { mediaFitsTrack, uploadFiles } from "../lib/importMedia";
import { listAssets, listBuiltinStickers, assetThumbnailUrl, assetMediaUrl, assetWaveformUrl, relinkAsset, updateAssetAudioRole, type AudioRole, type BuiltinSticker } from "../lib/mediaApi";
import { insertClipAutoTrack, swapClipAsset } from "../store/clipEdit";
import { getLatestState } from "../store/actions";
import { setFavorite } from "../store/effectEdit";
import { TitleLibrary } from "./TitleLibrary";
import { ResourcePanel } from "./ResourcePanel";
import { CaptionPanel } from "./CaptionPanel";
import { AssetPreview, type PreviewAsset } from "./AssetPreview";
import { StickerThumbnail } from "./StickerThumbnail";
import { MEDIA_ROW_HEIGHT, useVirtualMediaRows } from "./useVirtualMediaRows";
import { LOCATE_PREFLIGHT_ISSUE, type LocatePreflightIssueEvent } from "../lib/preflight";
import { MediaRelinkFolder } from "./MediaRelinkFolder";

interface ClipAsset {
  sourcePath: string;
  name: string;
  kind: "video" | "audio" | "image" | "unknown";
  duration: number | null;
}

type KindFilter = "all" | "video" | "audio" | "image";
type SourceFilter = "mine" | "builtin";
type UsageFilter = "all" | "used" | "unused";
type CreativeDomain = "media" | "music" | "text" | "sticker" | "effects" | "transition" | "caption" | "filter";
const CREATIVE_DOMAINS: { id: CreativeDomain; label: string }[] = [
  { id: "media", get label() { return tr("素材"); } },
  { id: "music", get label() { return tr("音乐"); } },
  { id: "text", get label() { return tr("文字"); } },
  { id: "sticker", get label() { return tr("贴纸"); } },
  { id: "effects", get label() { return tr("特效"); } },
  { id: "transition", get label() { return tr("转场"); } },
  { id: "caption", get label() { return tr("字幕"); } },
  { id: "filter", get label() { return tr("滤镜"); } },
];
const SOURCE_FILTER_ORDER: SourceFilter[] = ["mine", "builtin"];
const KIND_FILTERS: { value: KindFilter; label: string }[] = [
  { value: "all", get label() { return tr("全部"); } },
  { value: "video", get label() { return tr("视频"); } },
  { value: "audio", get label() { return tr("音频"); } },
  { value: "image", get label() { return tr("图片"); } },
];

export function MediaPanel() {
  const locale = useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const editLocked = Boolean(state.editLock);
  const [domain, setDomain] = useState<CreativeDomain>("media");
  const domainTabRefs = useRef(new Map<CreativeDomain, HTMLButtonElement>());
  const domainScrollPositions = useRef(new Map<CreativeDomain, number>());
  const [dragging, setDragging] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [transferKind, setTransferKind] = useState<"import" | "relink">("import");
  const [assetLoading, setAssetLoading] = useState(true);
  const [assetLoadError, setAssetLoadError] = useState("");
  const [assetLoadAttempt, setAssetLoadAttempt] = useState(0);
  const [importProgress, setImportProgress] = useState<{
    completed: number; total: number; name: string; succeeded: number; failed: number; done: boolean;
    loaded: number; totalBytes: number; cancelled?: boolean;
  } | null>(null);
  const [importFailures, setImportFailures] = useState<{ name: string; message: string }[]>([]);
  const [swappingPath, setSwappingPath] = useState<string | null>(null);
  const [relinkTarget, setRelinkTarget] = useState<{ assetId: string; name: string } | null>(null);
  const [relinkingId, setRelinkingId] = useState<string | null>(null);
  const [queryByDomain, setQueryByDomain] = useState<Record<CreativeDomain, string>>({
    media: "", music: "", text: "", sticker: "", effects: "", transition: "", caption: "", filter: "",
  });
  const query = queryByDomain[domain];
  const setQuery = (value: string) => setQueryByDomain((current) => ({ ...current, [domain]: value }));
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("mine");
  const [stickerCatalog, setStickerCatalog] = useState<BuiltinSticker[]>([]);
  const [stickerLoading, setStickerLoading] = useState(false);
  const [stickerLoadAttempt, setStickerLoadAttempt] = useState(0);
  const [stickerCategory, setStickerCategory] = useState("");
  const [stickerFavoritesOnly, setStickerFavoritesOnly] = useState(false);
  const [savingStickerFavorite, setSavingStickerFavorite] = useState<string | null>(null);
  const [stickerError, setStickerError] = useState("");
  const [addingSticker, setAddingSticker] = useState<string | null>(null);
  const [usageFilter, setUsageFilter] = useState<UsageFilter>("used");
  const [assetPreview, setAssetPreview] = useState<PreviewAsset | null>(null);
  useEffect(() => { setUsageFilter("used"); setAssetPreview(null); setAudition(null); }, [state.currentId]);
  const [audioRoleFilter, setAudioRoleFilter] = useState<"all" | AudioRole>("all");
  const [savingAudioRole, setSavingAudioRole] = useState<string | null>(null);
  const [audition, setAudition] = useState<{ assetId: string; name: string } | null>(null);
  const [auditionAttempt, setAuditionAttempt] = useState(0);
  const [auditionError, setAuditionError] = useState("");
  const [waveformError, setWaveformError] = useState(false);
  const [, force] = useState(0); // 监听会话资产变化
  const fileRef = useRef<HTMLInputElement | null>(null);
  const relinkFileRef = useRef<HTMLInputElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const uploadAbort = useRef<AbortController | null>(null);
  useEffect(() => () => uploadAbort.current?.abort(), []);
  const sourceTabRefs = useRef(new Map<SourceFilter, HTMLButtonElement>());

  useLayoutEffect(() => {
    const scroll = domainTabRefs.current.get(domain)?.closest(".zone-left__scroll");
    const saved = domainScrollPositions.current.get(domain);
    if (scroll instanceof HTMLElement && saved !== undefined) scroll.scrollTop = saved;
  }, [domain]);

  useEffect(() => {
    const player = audioRef.current;
    if (!player) return;
    if (!audition) {
      player.pause();
      return;
    }
    setAuditionError("");
    setWaveformError(false);
    window.dispatchEvent(new Event("cutvoke:pause-play"));
    player.load();
    void player.play().catch(() => {
      // Browser autoplay policy can require the user to press the native control.
    });
    return () => player.pause();
  }, [audition, auditionAttempt]);

  useEffect(() => {
    if (domain !== "music") setAudition(null);
  }, [domain]);

  useEffect(() => subscribeAssets(() => force((n) => n + 1)), []);

  useEffect(() => {
    const locate = (event: Event) => {
      const issue = (event as LocatePreflightIssueEvent).detail;
      if (issue.resourceKind !== "media") return;
      const asset = getSessionAssets().find((item) =>
        issue.assetIds?.includes(item.assetId) || item.path === issue.path,
      );
      domainScrollPositions.current.set("media", 0);
      setDomain("media");
      setSourceFilter("mine");
      setKindFilter("all");
      setUsageFilter("all");
      setQueryByDomain((current) => ({ ...current, media: asset?.name || sourceBasename(issue.path || "") }));
      setAssetLoadAttempt((attempt) => attempt + 1);
      dispatch({
        type: "STATUS_SET", severity: "warn",
        text: asset
          ? msg("请为「{0}」重新链接素材文件", asset.name)
          : msg("请导入替代素材，再替换当前选中的片段"),
      });
      requestAnimationFrame(() => {
        const scroll = domainTabRefs.current.get("media")?.closest(".zone-left__scroll");
        if (scroll instanceof HTMLElement) scroll.scrollTop = 0;
        document.querySelector<HTMLInputElement>('[data-testid="media-search"]')?.focus();
      });
    };
    window.addEventListener(LOCATE_PREFLIGHT_ISSUE, locate);
    return () => window.removeEventListener(LOCATE_PREFLIGHT_ISSUE, locate);
  }, [dispatch]);

  // 挂载时拉取服务端素材库（D02：素材刷新不丢）。幂等：已存在会话内的项不会被覆盖。
  useEffect(() => {
    let cancelled = false;
    setAssetLoading(true);
    setAssetLoadError("");
    (async () => {
      const res = await listAssets();
      if (cancelled) return;
      if (res.kind !== "ok") {
        setAssetLoadError(res.message);
        return;
      }
      setAssetLoadError("");
      hydrateAssets(
        res.data.map((a) => ({
          assetId: a.assetId,
          path: a.path,
          name: a.name,
          size: a.size,
          kind: a.kind,
          duration: a.duration,
          available: a.available,
          audioRole: a.audioRole,
        })),
      );
    })().finally(() => {
      if (!cancelled) setAssetLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [assetLoadAttempt]);

  useEffect(() => {
    let cancelled = false;
    setStickerLoading(true);
    setStickerError("");
    void listBuiltinStickers().then((items) => {
      if (!cancelled) setStickerCatalog(items);
    }).catch((error) => {
      if (!cancelled) setStickerError(error instanceof Error ? error.message : String(error));
    }).finally(() => {
      if (!cancelled) setStickerLoading(false);
    });
    return () => { cancelled = true; };
  }, [stickerLoadAttempt]);

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

  const selectDomain = (next: CreativeDomain) => {
    if (next === domain) return;
    const scroll = domainTabRefs.current.get(domain)?.closest(".zone-left__scroll");
    if (scroll instanceof HTMLElement) domainScrollPositions.current.set(domain, scroll.scrollTop);
    setDomain(next);
  };

  useEffect(() => {
    const open = (event: Event) => {
      const next = (event as CustomEvent<CreativeDomain>).detail;
      if (CREATIVE_DOMAINS.some(item => item.id === next)) {
        selectDomain(next);
        requestAnimationFrame(() => { domainTabRefs.current.get(next)?.scrollIntoView({ block: "nearest" }); domainTabRefs.current.get(next)?.focus(); });
      }
    };
    window.addEventListener("cutvoke:open-creative-domain", open);
    return () => window.removeEventListener("cutvoke:open-creative-domain", open);
  }, [domain]);

  const onDomainTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const key = readingOrderKey(event.key, locale);
    let nextIndex: number | null = null;
    if (key === "ArrowRight" || key === "ArrowDown") nextIndex = (index + 1) % CREATIVE_DOMAINS.length;
    else if (key === "ArrowLeft" || key === "ArrowUp") {
      nextIndex = (index - 1 + CREATIVE_DOMAINS.length) % CREATIVE_DOMAINS.length;
    } else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = CREATIVE_DOMAINS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = CREATIVE_DOMAINS[nextIndex].id;
    selectDomain(next);
    requestAnimationFrame(() => domainTabRefs.current.get(next)?.focus());
  };

  const onSourceTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const key = readingOrderKey(event.key, locale);
    let nextIndex: number | null = null;
    if (key === "ArrowRight" || key === "ArrowDown") nextIndex = (index + 1) % SOURCE_FILTER_ORDER.length;
    else if (key === "ArrowLeft" || key === "ArrowUp") {
      nextIndex = (index - 1 + SOURCE_FILTER_ORDER.length) % SOURCE_FILTER_ORDER.length;
    } else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = SOURCE_FILTER_ORDER.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = SOURCE_FILTER_ORDER[nextIndex];
    setSourceFilter(next);
    requestAnimationFrame(() => sourceTabRefs.current.get(next)?.focus());
  };

  // 工程 clips 提取的源文件（复用已有素材）
  const clipAssets = useMemo<ClipAsset[]>(() => {
    const map = new Map<string, ClipAsset>();
    const tracks = [state.project?.sequence, ...(state.project?.sequences || [])].flatMap(sequence => sequence?.tracks || []);
    for (const t of tracks) {
      if (t.kind === "text") continue;
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
  }, [state.project, locale]);

  const timelineUses = useMemo(() => {
    const locations = new Map<string, { trackId: string; clipId: string; time: number }[]>();
    for (const track of state.project?.sequence.tracks || []) for (const clip of track.clips) {
      const use = { trackId: track.id, clipId: clip.id, time: Number(clip.timelineStart.num) / Number(clip.timelineStart.den) };
      for (const key of [`path:${clip.assetRef.sourcePath}`, ...(clip.assetRef.assetId ? [`id:${clip.assetRef.assetId}`] : [])]) {
        const list = locations.get(key) || []; list.push(use); locations.set(key, list);
      }
    }
    for (const list of locations.values()) list.sort((a, b) => a.time - b.time);
    return locations;
  }, [state.project]);

  const sessionAssets = getSessionAssets();
  const assets: { path: string; name: string; kind: ClipAsset["kind"]; duration: number | null; assetId?: string; builtIn: boolean; available?: boolean; audioRole?: AudioRole }[] = useMemo(() => {
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
        audioRole: "unclassified" as const,
      }));
    return [
      ...sessionAssets.map((a) => ({
        path: a.path,
        name: a.name,
        kind: a.kind,
        duration: a.duration,
        assetId: a.assetId,
        builtIn: isBuiltInAsset(a.name, a.path),
        available: a.available,
        audioRole: a.audioRole || "unclassified",
      })),
      ...extra,
    ];
  }, [sessionAssets, clipAssets, locale]);
  const sourceAssetsForDomain = assets.filter((asset) =>
    domain === "music" ? asset.kind === "audio" : asset.kind !== "audio",
  );
  const mineCount = sourceAssetsForDomain.filter((asset) => !asset.builtIn).length;
  const builtInCount = sourceAssetsForDomain.filter((asset) =>
    asset.builtIn && !asset.assetId?.startsWith("builtin_sticker_"),
  ).length;
  const selectedSourceCount = sourceFilter === "builtin" ? builtInCount : mineCount;
  const stickerCategories = [...new Set(stickerCatalog.map((item) => item.subcategory))];
  const visibleStickers = stickerCatalog.filter((item) => {
    if (stickerCategory && item.subcategory !== stickerCategory) return false;
    if (stickerFavoritesOnly && !state.project?.favorites?.includes(item.stickerId)) return false;
    const needle = query.trim().toLocaleLowerCase();
    return !needle || [item.name, item.subcategory, ...item.keywords]
      .some((part) => part.toLocaleLowerCase().includes(needle));
  });

  // 已用判定：素材的 path / assetId 出现在任意轨道 clip 的 assetRef 中（D02）。
  const usedRefs = useMemo(() => {
    const paths = new Set<string>();
    const ids = new Set<string>();
    for (const t of [state.project?.sequence, ...(state.project?.sequences || [])].flatMap(sequence => sequence?.tracks || [])) {
      for (const c of t.clips || []) {
        if (c.assetRef?.sourcePath) paths.add(c.assetRef.sourcePath);
        if (c.assetRef?.assetId) ids.add(c.assetRef.assetId);
      }
    }
    return { paths, ids };
  }, [state.project, locale]);

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
        if (sourceFilter === "builtin" && a.assetId?.startsWith("builtin_sticker_")) return false;
        if (q && !a.name.toLowerCase().includes(q)) return false;
        if (domain === "media" && kindFilter !== "all" && a.kind !== kindFilter) return false;
        if (domain === "music" && audioRoleFilter !== "all" &&
            (a.audioRole || "unclassified") !== audioRoleFilter) return false;
        if (sourceFilter === "mine" && usageFilter === "used" && !a.used) return false;
        if (sourceFilter === "mine" && usageFilter === "unused" && a.used) return false;
        return true;
      });
  }, [assets, query, kindFilter, sourceFilter, usageFilter, usedRefs, domain, audioRoleFilter, locale]);

  const domainAssets = useMemo(
    () => filtered.filter((asset) => domain === "music" ? asset.kind === "audio" : asset.kind !== "audio"),
    [filtered, domain, locale],
  );
  const mediaRowHeight = domain === "music" ? 90 : MEDIA_ROW_HEIGHT;
  const mediaRows = useVirtualMediaRows(domainAssets.length, domain === "media" || domain === "music", mediaRowHeight);

  const handleFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    if (editLocked) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: msg("Agent 正在编辑，素材导入已暂停") });
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    setImporting(true);
    setTransferKind("import");
    setImportFailures([]);
    const controller = new AbortController(); uploadAbort.current = controller;
    setImportProgress({ completed: 0, total: fileList.length, name: fileList[0].name, succeeded: 0, failed: 0,
      done: false, loaded: 0, totalBytes: fileList[0].size });
    try {
      const ok = await uploadFiles(
        Array.from(fileList),
        () => {},
        (name, message) => {
          setImportFailures((failures) => [...failures, { name, message }]);
          dispatch({ type: "STATUS_SET", severity: "warn", text: msg("导入 {0} 失败：{1}", name, message) });
        },
        (progress) => setImportProgress({ ...progress, done: false }),
        { signal: controller.signal },
      );
      setImportProgress((progress) => progress ? { ...progress, done: true, cancelled: controller.signal.aborted } : null);
      if (ok > 0) {
        setSourceFilter("mine"); setUsageFilter("unused"); setKindFilter("all"); setQuery("");
        dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已导入 {0} 个素材", ok) });
      }
    } catch (error) {
      setImportProgress((progress) => progress ? { ...progress, done: true, failed: progress.failed + 1 } : null);
      setImportFailures((failures) => [...failures, {
        name: tr("批量导入"),
        message: error instanceof Error ? error.message : String(error),
      }]);
      dispatch({
        type: "STATUS_SET",
        severity: "err",
        text: msg("素材导入失败：{0}", error instanceof Error ? error.message : String(error)),
      });
    } finally {
      setImporting(false);
      uploadAbort.current = null;
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const startDrag = (e: React.DragEvent, a: { sourcePath: string; assetId?: string; kind: ClipAsset["kind"];
    role?: "sticker"; stickerAnimation?: { effectId: string; params: Record<string, unknown> };
    resourceRef?: BuiltinSticker["resourceRef"]; stickerScale?: number }) => {
    if (editLocked) {
      e.preventDefault();
      return;
    }
    e.dataTransfer.setData("text/cutvoke-media", JSON.stringify({
      sourcePath: a.sourcePath,
      assetId: a.assetId,
      kind: a.kind,
      ...(a.role ? { role: a.role } : {}),
      ...(a.stickerAnimation ? { stickerAnimation: a.stickerAnimation } : {}),
      ...(a.resourceRef ? { resourceRef: a.resourceRef } : {}),
      ...(a.stickerScale !== undefined ? { stickerScale: a.stickerScale } : {}),
    }));
    e.dataTransfer.effectAllowed = "copy";
    setDragging(a.sourcePath);
  };
  const endDrag = () => setDragging(null);

  const addSticker = async (sticker: BuiltinSticker) => {
    const asset = assets.find((item) => item.assetId === sticker.assetId);
    if (!state.currentId || editLocked || !sticker.available || !asset || addingSticker) return;
    setAddingSticker(sticker.stickerId);
    try {
      const latest = getLatestState() || state;
      const result = await insertClipAutoTrack(dispatch, latest, {
        sourcePath: asset.path, assetId: sticker.assetId,
        trackKind: "video", trackRole: "sticker",
        timelineStartSecs: latest.playhead,
        stickerAnimation: sticker.kind === "dynamic"
          ? { effectId: sticker.effectId, params: sticker.params } : undefined,
        resourceRef: sticker.resourceRef || undefined,
        stickerScale: sticker.defaultScale,
      });
      if (result.ok) {
        const changes = result.command?.changedEntities || [];
        const trackId = changes.find((item) => item.type === "track" && item.change === "created")?.id;
        const clipId = changes.find((item) => item.type === "clip" && item.change === "created")?.id;
        if (trackId && clipId) dispatch({ type: "SELECTION_SET", selection: { trackId, clipId } });
        dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已添加贴纸「{0}」到独立叠加轨", sticker.name) });
      }
    } finally {
      setAddingSticker(null);
    }
  };

  const toggleStickerFavorite = async (sticker: BuiltinSticker) => {
    if (!state.currentId || editLocked || savingStickerFavorite) return;
    setSavingStickerFavorite(sticker.stickerId);
    try {
      const latest = getLatestState() || state;
      await setFavorite(dispatch, latest, {
        stickerId: sticker.stickerId,
        favorite: !latest.project?.favorites?.includes(sticker.stickerId),
      });
    } finally {
      setSavingStickerFavorite(null);
    }
  };

  // 选中片段 id（时间线选中才能换图套版）
  const selectedClipId = state.selection?.clipId;
  const selectedTrack = state.project?.sequence.tracks.find((track) => track.id === state.selection?.trackId);
  const selectedTrackLocked = Boolean(selectedTrack?.locked);

  const swapDisabledReason = (kind: ClipAsset["kind"], available = true): string | null => {
    if (editLocked) return tr("Agent 正在编辑，暂不可替换素材");
    if (!available) return tr("素材文件丢失，请先重新链接");
    if (!selectedClipId || !selectedTrack) return tr("请先在时间线选中片段");
    if (selectedTrackLocked) return tr("所选轨道已锁定，解锁后才能替换素材");
    if (!mediaFitsTrack(kind, selectedTrack.kind)) {
      return selectedTrack.kind === "audio"
        ? tr("音频轨只能替换为音频素材")
        : tr("视频轨不能替换为音频素材");
    }
    return null;
  };

  // 换图套版：对当前选中片段调 asset.swap，仅替换素材引用，保留时长/动画/效果。
  const handleSwap = async (a: { path: string; assetId?: string; kind: ClipAsset["kind"]; available?: boolean }) => {
    const blocked = swapDisabledReason(a.kind, a.available !== false);
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

  const handleRelink = async (files: FileList | null) => {
    const target = relinkTarget;
    const file = files?.item(0);
    if (!target || !file || editLocked) return;
    setRelinkingId(target.assetId);
    const controller = new AbortController(); uploadAbort.current = controller;
    setImporting(true); setTransferKind("relink"); setImportFailures([]);
    setImportProgress({ completed: 0, total: 1, name: file.name, succeeded: 0, failed: 0, done: false, loaded: 0, totalBytes: file.size });
    try {
      const result = await relinkAsset(target.assetId, file, { signal: controller.signal,
        onProgress: (loaded, totalBytes) => setImportProgress(progress => progress ? { ...progress, loaded, totalBytes } : null) });
      if (controller.signal.aborted) return;
      if (result.kind === "error") {
        setImportProgress(progress => progress ? { ...progress, completed: 1, failed: 1 } : null);
        setImportFailures([{ name: target.name, message: result.message }]);
        dispatch({ type: "STATUS_SET", severity: "err", text: msg("重新链接 {0} 失败：{1}", target.name, result.message) });
        return;
      }
      setImportProgress(progress => progress ? { ...progress, completed: 1, succeeded: 1, loaded: file.size } : null);
      hydrateAssets([{ assetId: result.data.assetId, path: result.data.path,
                       name: result.data.name, size: result.data.size,
                       kind: result.data.kind, duration: result.data.duration,
                       available: true }]);
      setQueryByDomain((queries) => ({
        ...queries,
        media: queries.media === target.name ? result.data.name : queries.media,
        music: queries.music === target.name ? result.data.name : queries.music,
      }));
      window.dispatchEvent(new CustomEvent("cutvoke:asset-restored", {
        detail: { assetId: result.data.assetId, path: result.data.path },
      }));
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已重新链接 {0}", result.data.name) });
    } finally {
      setImporting(false); uploadAbort.current = null;
      setImportProgress(progress => progress ? { ...progress, done: true, cancelled: controller.signal.aborted } : null);
      setRelinkingId(null);
      setRelinkTarget(null);
      if (relinkFileRef.current) relinkFileRef.current.value = "";
    }
  };

  const handleAudioRoleChange = async (assetId: string, audioRole: AudioRole) => {
    if (editLocked || savingAudioRole) return;
    setSavingAudioRole(assetId);
    const result = await updateAssetAudioRole(assetId, audioRole);
    if (result.kind === "error") {
      dispatch({ type: "STATUS_SET", severity: "err", text: msg("音频分类保存失败：{0}", result.message) });
    } else {
      updateSessionAssetAudioRole(assetId, result.data.audioRole);
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("音频分类已保存") });
    }
    setSavingAudioRole(null);
  };

  return (
    <Panel title={tr("创作资源")}>
      <div className="media-domain-tabs" role="tablist" aria-label={tr("创作域")}>
        {CREATIVE_DOMAINS.map((item, index) => (
          <button key={item.id} type="button" role="tab"
            id={`creative-domain-${item.id}`} aria-controls="creative-domain-content"
            aria-selected={domain === item.id} tabIndex={domain === item.id ? 0 : -1}
            ref={(element) => {
              if (element) domainTabRefs.current.set(item.id, element);
              else domainTabRefs.current.delete(item.id);
            }}
            className={domain === item.id ? "media-domain-tab media-domain-tab--active" : "media-domain-tab"}
            onClick={() => selectDomain(item.id)}
            onKeyDown={(event) => onDomainTabKeyDown(event, index)}>
            {item.label}
          </button>
        ))}
      </div>
      <div id="creative-domain-content" role="tabpanel"
        aria-labelledby={`creative-domain-${domain}`} tabIndex={0}>
      {(domain === "media" || domain === "music") ? <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <Button
          variant="primary"
          full
          onClick={openImport}
          disabled={importing || editLocked}
          style={{ height: 30 }}
          aria-label={tr("导入素材")}
        >
          {importing ? <Loader2 size={14} className="cv-spin" /> : <Upload size={14} />}
          {importing ? tr("导入中…") : tr("导入文件")}
        </Button>
      </div> : null}
        <input
          ref={fileRef}
          type="file"
          accept="video/*,audio/*,image/*"
          multiple
          style={{ display: "none" }}
          onChange={(e) => handleFiles(e.target.files)}
        />
        <input
          ref={relinkFileRef}
          type="file"
          accept="video/*,audio/*,image/*"
          style={{ display: "none" }}
          aria-label={tr("选择重新链接文件")}
          onChange={(e) => handleRelink(e.target.files)}
        />
      {(domain === "media" || domain === "music") && importProgress ? (
        <div className="media-import-progress" role="status">
          <span>
            {importProgress.cancelled ? tr("上传已取消，后续文件未上传") : importProgress.done
              ? transferKind === "relink" ? tr("重新链接结束：成功 {0}，失败 {1}", importProgress.succeeded, importProgress.failed)
                : tr("导入结束：成功 {0}，失败 {1}", importProgress.succeeded, importProgress.failed)
              : tr("导入 {0}/{1}{2}", importProgress.completed, importProgress.total, importProgress.name ? tr(" · {0}", importProgress.name) : "")}
          </span>
          <progress value={importProgress.completed} max={importProgress.total} />
          {!importProgress.done ? <>
            <span>{tr("当前文件：{0}", importProgress.name)}</span>
            <span>{tr("已传 {0} / {1} MiB", (importProgress.loaded / 1048576).toFixed(1), (importProgress.totalBytes / 1048576).toFixed(1))}</span>
            <progress aria-label={tr("当前文件上传进度")} value={importProgress.loaded} max={importProgress.totalBytes || 1} />
            <button type="button" className="cv-btn cv-btn--secondary" onClick={() => uploadAbort.current?.abort()}>{tr("取消当前及后续上传")}</button>
          </> : null}
          {importFailures.length ? (
            <ul className="media-import-progress__failures">
              {importFailures.map((failure, index) => (
                <li key={`${failure.name}-${index}`}>{failure.name}：{failure.message}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {(domain === "media" || domain === "music") ? <MediaRelinkFolder disabled={editLocked || importing}
        onRestored={() => setAssetLoadAttempt(attempt => attempt + 1)} /> : null}
      {(domain === "media" || domain === "music") ? <div className="media-sources" role="tablist" aria-label={tr("素材来源")}>
        <button
          type="button"
          role="tab"
          id="media-source-mine"
          aria-controls="creative-domain-content"
          aria-selected={sourceFilter === "mine"}
          tabIndex={sourceFilter === "mine" ? 0 : -1}
          ref={(element) => {
            if (element) sourceTabRefs.current.set("mine", element);
            else sourceTabRefs.current.delete("mine");
          }}
          className={sourceFilter === "mine" ? "media-source--active" : ""}
          onClick={() => setSourceFilter("mine")}
          onKeyDown={(event) => onSourceTabKeyDown(event, 0)}
        >{tr("我的素材 ")}<span>{mineCount}</span>
        </button>
        <button
          type="button"
          role="tab"
          id="media-source-builtin"
          aria-controls="creative-domain-content"
          aria-selected={sourceFilter === "builtin"}
          tabIndex={sourceFilter === "builtin" ? 0 : -1}
          ref={(element) => {
            if (element) sourceTabRefs.current.set("builtin", element);
            else sourceTabRefs.current.delete("builtin");
          }}
          className={sourceFilter === "builtin" ? "media-source--active" : ""}
          onClick={() => setSourceFilter("builtin")}
          onKeyDown={(event) => onSourceTabKeyDown(event, 1)}
        >{tr("内置资源 ")}<span>{builtInCount}</span>
        </button>
      </div> : null}
      {(domain === "media" || domain === "music") && assetLoadError ? (
        <div className="resource-preview__failure" role="alert">
          <p>{tr("素材库读取失败：")}{assetLoadError}</p>
          <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
            disabled={assetLoading} onClick={() => setAssetLoadAttempt((attempt) => attempt + 1)}>
            {assetLoading ? tr("正在重试…") : tr("重试素材库")}
          </button>
        </div>
      ) : null}
      {(domain === "media" || domain === "music") && assetLoading ? (
        <p className="cv-loading" role="status">{tr("正在读取素材库…")}</p>
      ) : null}
      {(domain === "media" || domain === "music" || domain === "text" || domain === "sticker") ? <div className="media-filter">
        <label className="media-filter__search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            data-testid="media-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={domain === "sticker" ? tr("搜索贴纸、用途…") : domain === "text" ? tr("搜索标题样式…") : domain === "music" ? tr("搜索音乐与音效…") : tr("搜索素材名…")}
            aria-label={domain === "sticker" ? tr("搜索贴纸") : domain === "text" ? tr("搜索标题样式") : domain === "music" ? tr("搜索音乐与音效") : tr("搜索素材")}
          />
        </label>
        {(domain === "media" || domain === "music") ? <>
        {domain === "media" ? <>
        <div className="media-filter__kinds" role="group" aria-label={tr("素材类型过滤")}>
          {KIND_FILTERS.filter((option) => option.value !== "audio").map((opt) => (
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
        </> : null}
        {sourceFilter === "mine" && <div className="media-filter__kinds media-filter__usage" role="group" aria-label={tr("使用状态过滤")}>
          {(["all", "used", "unused"] as const).map((value) => (
            <button
              key={value}
              type="button"
              className={`media-filter__kind ${usageFilter === value ? "media-filter__kind--active" : ""}`}
              aria-pressed={usageFilter === value}
              onClick={() => setUsageFilter(value)}
            >
              {{ all: tr("全部素材"), used: tr("当前工程已用"), unused: tr("当前工程未用") }[value]}
              <span> {sourceAssetsForDomain.filter(asset => !asset.builtIn && (value === "all" ||
                (usedRefs.paths.has(asset.path) || !!asset.assetId && usedRefs.ids.has(asset.assetId)) === (value === "used"))).length}</span>
            </button>
          ))}
        </div>}
        {domain === "music" ? (
          <div className="media-filter__kinds" role="group" aria-label={tr("音频用途过滤")}>
            {([
              ["all", tr("全部用途")], ["music", tr("背景音乐")],
              ["sound_effect", tr("音效")], ["unclassified", tr("未分类")],
            ] as const).map(([value, label]) => (
              <button key={value} type="button"
                className={`media-filter__kind ${audioRoleFilter === value ? "media-filter__kind--active" : ""}`}
                aria-pressed={audioRoleFilter === value}
                onClick={() => setAudioRoleFilter(value)}>
                {label}
              </button>
            ))}
          </div>
        ) : null}
        </> : null}
      </div> : null}
      {domain === "text" ? <TitleLibrary query={query} /> : domain === "sticker" ? (
        <section className="sticker-library" aria-label={tr("贴纸库")}>
          <div className="sticker-library__head">
            <strong>{tr("静态与动态贴纸")}</strong>
            <span>{visibleStickers.length} / {stickerCatalog.length}</span>
          </div>
          <p className="cv-hint">{tr("透明 PNG 与真实动画样片 · 添加到独立叠加轨，可调位置、缩放、旋转、时长与动画。")}</p>
          <div className="sticker-library__filters">
            <label className="sticker-library__category">
              <span>{tr("分类")}</span>
              <select aria-label={tr("贴纸分类")} value={stickerCategory}
                onChange={(event) => setStickerCategory(event.currentTarget.value)}>
                <option value="">{tr("全部 · ")}{stickerCatalog.length}</option>
                {stickerCategories.map((category) => (
                  <option key={category} value={category}>
                    {category} · {stickerCatalog.filter((item) => item.subcategory === category).length}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className={`sticker-library__favorite${stickerFavoritesOnly ? " sticker-library__favorite--active" : ""}`}
              aria-pressed={stickerFavoritesOnly} onClick={() => setStickerFavoritesOnly((value) => !value)}>{tr("仅看收藏")}</button>
          </div>
          {stickerLoading ? <p className="cv-loading" role="status">{tr("贴纸库加载中…")}</p> : null}
          {stickerError ? (
            <div className="resource-preview__failure" role="alert">
              <p>{tr("贴纸库读取失败：")}{stickerError}</p>
              <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
                disabled={stickerLoading} onClick={() => setStickerLoadAttempt((attempt) => attempt + 1)}>
                {stickerLoading ? tr("正在重试…") : tr("重试贴纸库")}
              </button>
            </div>
          ) : null}
          <div className="sticker-grid">
            {visibleStickers.map((sticker) => {
              const asset = assets.find((item) => item.assetId === sticker.assetId);
              const available = sticker.available && asset?.available !== false && !!asset;
              return (
                <article key={sticker.stickerId} className={`sticker-card${available ? "" : " sticker-card--missing"}`}
                  draggable={available && !editLocked}
                  onDragStart={(event) => asset && startDrag(event, { sourcePath: asset.path,
                    assetId: sticker.assetId, kind: "image", role: "sticker",
                    resourceRef: sticker.resourceRef || undefined,
                    stickerScale: sticker.defaultScale,
                    stickerAnimation: sticker.kind === "dynamic"
                      ? { effectId: sticker.effectId, params: sticker.params } : undefined })}
                  onDragEnd={endDrag}>
                  <div className="sticker-card__preview">
                    {available ? <StickerThumbnail sticker={sticker} />
                        : <span>{sticker.availabilityMessage || tr("资源不可用，可恢复资源包或选择其他贴纸。")}</span>}
                    <button type="button" className="sticker-card__favorite"
                      aria-label={tr("{0}贴纸 {1}", state.project?.favorites?.includes(sticker.stickerId) ? tr("取消收藏") : tr("收藏"), sticker.name)}
                      aria-pressed={Boolean(state.project?.favorites?.includes(sticker.stickerId))}
                      disabled={editLocked || !state.currentId || savingStickerFavorite === sticker.stickerId}
                      onClick={(event) => { event.stopPropagation(); void toggleStickerFavorite(sticker); }}>
                      <Star size={13} fill={state.project?.favorites?.includes(sticker.stickerId) ? "currentColor" : "none"} />
                    </button>
                  </div>
                  <strong>{sticker.name}</strong>
                  <small>{sticker.kind === "dynamic" ? tr("动态") : tr("静态")}{tr(" · 悬停预览 · ")}{sticker.subcategory} · {sticker.qualified ? tr("合格") : tr("候选")}{tr(" · 资源包 v")}{sticker.resourceRef?.packVersion || tr("未知")} · {sticker.license}</small>
                  <details className="sticker-card__provenance">
                    <summary>{sticker.source ? tr("来源信息") : tr("来源待补")}</summary>
                    <p>{sticker.source || tr("此贴纸没有可复核的来源记录。")}</p>
                  </details>
                  <button type="button" onClick={() => void addSticker(sticker)}
                    disabled={!available || editLocked || !state.currentId || !!addingSticker}
                    aria-label={tr("添加贴纸 {0}", sticker.name)}>
                    {addingSticker === sticker.stickerId ? tr("添加中…") : tr("添加")}
                  </button>
                </article>
              );
            })}
          </div>
          {!stickerError && !stickerLoading && visibleStickers.length === 0 ? <p className="cv-empty">{tr("没有匹配的贴纸。")}</p> : null}
        </section>
      ) : (domain === "media" || domain === "music") ? (
        selectedSourceCount === 0 && (assetLoading || assetLoadError) ? null : selectedSourceCount === 0 ? (
        <div className="media-empty">
          <p className="cv-empty">
            {sourceFilter === "mine"
              ? domain === "music" ? tr("还没有导入音乐或音效。") : tr("还没有导入自己的视频或图片。")
              : domain === "music" ? tr("当前没有内置音乐或音效。") : tr("当前没有内置视频或图片。")}
            {domain === "music" && sourceFilter === "mine"
              ? tr(" 点击上方「导入文件」添加音频。")
              : sourceFilter === "mine" ? tr(" 点击上方「导入文件」添加素材。") : ""}
          </p>
        </div>
        ) : domainAssets.length === 0 ? (
        <div className="media-empty">
          <p className="cv-empty">
            {domain === "music"
              ? tr("没有匹配的音乐或音效。试试调整搜索词或使用状态筛选。")
              : tr("没有匹配的视频或图片。试试调整搜索词、类型或使用状态筛选。")}
          </p>
          <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary" onClick={() => {
            setQuery(""); setKindFilter("all"); setAudioRoleFilter("all");
            setUsageFilter(sourceFilter === "mine" && usageFilter === "used" && !query.trim() ? "unused" : "all");
          }}>{sourceFilter === "mine" && usageFilter === "used" && !query.trim()
            ? tr("查看未使用素材") : tr("清除筛选")}</button>
        </div>
        ) : (
        <div className="media-panel" ref={mediaRows.ref}>
          {mediaRows.before > 0 ? <div aria-hidden="true" style={{ height: mediaRows.before - 6, flexShrink: 0 }} /> : null}
          {domainAssets.slice(mediaRows.start, mediaRows.end).map((a) => {
            const disabledReason = swapDisabledReason(a.kind, a.available !== false);
            const locations = timelineUses.get(`path:${a.path}`) || timelineUses.get(`id:${a.assetId}`) || [];
            return (
            <div
              key={a.path + a.name}
              style={mediaRows.virtual ? { height: mediaRowHeight, boxSizing: "border-box", flexShrink: 0 } : undefined}
              className={`media-item ${dragging === a.path ? "media-item--dragging" : ""} ${a.available === false ? "media-item--missing" : ""}`}
              draggable={!editLocked && a.available !== false}
              onDragStart={(e) => startDrag(e, { sourcePath: a.path, assetId: a.assetId, kind: a.kind })}
              onDragEnd={endDrag}
              title={`${a.name}\n${a.path}${disabledReason && selectedClipId ? `\n${disabledReason}` : ""}`}
            >
              <button type="button" className="media-item__open" disabled={a.available === false || a.kind === "unknown"}
                aria-label={tr("预览素材 {0}", a.name)}
                onClick={() => {
                  setAudition(null);
                  setAssetPreview({ name: a.name, kind: a.kind, url: a.assetId ? assetMediaUrl(a.assetId)
                    : `/api/v1/projects/${encodeURIComponent(state.currentId!)}/source-media?${new URLSearchParams({ path: a.path })}` });
                }}>
              {a.assetId && a.available !== false && (a.kind === "image" || a.kind === "video") ? (
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
                  {a.available === false ? tr("文件丢失") : a.duration ? `${a.duration.toFixed(1)}s` : assetKindLabel(a.kind)}
                </span>
                {domain === "music" && a.kind === "audio" && a.builtIn ? (
                  <span className="media-item__provenance"
                    aria-label={tr("来源：CutVoke 内置 FFmpeg lavfi 合成脚本；授权：MIT")}
                    title={tr("来源：CutVoke 内置 FFmpeg lavfi 合成脚本；授权：MIT（仓库 LICENSE）")}>{tr("来源 CutVoke 合成 · MIT")}</span>
                ) : null}
              </div>
              </button>
              {a.used ? <button type="button" className="media-badge media-badge--locate"
                aria-label={tr("定位素材 {0}", a.name)} disabled={!locations.length}
                title={locations.length ? tr("点击定位素材，再次点击跳到下一处使用位置") : tr("当前时间线未使用此素材")}
                onClick={(event) => {
                  event.stopPropagation();
                  const selected = locations.findIndex((item) => item.clipId === state.selection?.clipId);
                  const next = locations[(selected + 1) % locations.length];
                  if (!next) return;
                  dispatch({ type: "SELECTION_SET", selection: { trackId: next.trackId, clipId: next.clipId } });
                  dispatch({ type: "PLAYHEAD_SET", t: next.time });
                  window.dispatchEvent(new CustomEvent("cutvoke:locate-clip", { detail: next }));
                }}>{tr("已用")}{locations.length > 1 ? ` ×${locations.length}` : ""}</button> : null}
              {a.available === false ? <span className="media-badge media-badge--missing">{tr("丢失")}</span> : null}
              {domain === "music" && a.assetId ? (
                <select
                  className="media-item__audio-role"
                  aria-label={tr("音频用途 {0}", a.name)}
                  title={a.builtIn ? tr("内置音频分类由资源清单维护") : tr("设置为背景音乐、音效或未分类")}
                  value={a.audioRole || "unclassified"}
                  disabled={a.builtIn || editLocked || savingAudioRole !== null}
                  onMouseDown={(event) => event.stopPropagation()}
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => void handleAudioRoleChange(a.assetId!, event.currentTarget.value as AudioRole)}>
                  <option value="music">{tr("背景音乐")}</option>
                  <option value="sound_effect">{tr("音效")}</option>
                  <option value="unclassified">{tr("未分类")}</option>
                </select>
              ) : null}
              {a.available === false && a.assetId && !a.builtIn ? (
                <button
                  type="button"
                  className="media-item__relink"
                  disabled={editLocked || relinkingId === a.assetId}
                  aria-label={tr("重新链接 {0}", a.name)}
                  title={tr("选择同类型文件恢复原素材引用")}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    setRelinkTarget({ assetId: a.assetId!, name: a.name });
                    relinkFileRef.current?.click();
                  }}
                >
                  {relinkingId === a.assetId ? <Loader2 size={14} className="cv-spin" /> : <Replace size={14} />}
                </button>
              ) : null}
              {a.kind === "audio" && a.assetId && a.available !== false ? (
                <button
                  type="button"
                  className="media-item__audition"
                  aria-label={tr("{0} {1}", audition?.assetId === a.assetId ? tr("停止试听") : tr("试听"), a.name)}
                  title={tr("试听音频并查看波形")}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    setAudition(audition?.assetId === a.assetId ? null : { assetId: a.assetId!, name: a.name });
                  }}
                >
                  {audition?.assetId === a.assetId ? <Pause size={14} /> : <Play size={14} />}
                </button>
              ) : null}
              <button
                type="button"
                className={`media-item__swap ${disabledReason ? "media-item__swap--disabled" : ""}`}
                disabled={Boolean(disabledReason) || swappingPath === a.path}
                aria-label={disabledReason || tr("替换选中片段")}
                title={disabledReason || tr("替换素材，保留片段的时长与效果")}
                onMouseDown={(e) => e.stopPropagation()}
                  onClick={(event) => { event.stopPropagation(); void handleSwap(a); }}
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
          {mediaRows.after > 0 ? <div aria-hidden="true" style={{ height: mediaRows.after - 6, flexShrink: 0 }} /> : null}
        </div>
        )
      ) : null}
      <div hidden={domain !== "effects"}>
        <ResourcePanel active={domain === "effects"} family="fx" />
      </div>
      {assetPreview && <AssetPreview key={`${state.currentId}:${assetPreview.url}`} asset={assetPreview} onClose={() => setAssetPreview(null)} />}
      <div hidden={domain !== "transition"}>
        <ResourcePanel active={domain === "transition"} family="transition" />
      </div>
      <div hidden={domain !== "filter"}>
        <ResourcePanel active={domain === "filter"} family="filter" />
      </div>
      <div hidden={domain !== "caption"}>
        <CaptionPanel />
      </div>
      {audition && domain === "music" ? (
        <section className="media-audition" aria-label={tr("试听 {0}", audition.name)}>
          <div className="media-audition__header">
            <Music size={14} aria-hidden="true" />
            <strong>{audition.name}</strong>
            <button type="button" onClick={() => setAudition(null)} aria-label={tr("关闭试听")}>{tr("关闭")}</button>
          </div>
          {waveformError ? (
            <p className="media-audition__error">{tr("波形暂不可用，可继续使用下方播放器试听。")}</p>
          ) : (
            <button
              type="button"
              className="media-audition__waveform"
              aria-label={tr("在波形上定位播放位置")}
              onClick={(e) => {
                const audio = audioRef.current;
                if (!audio || !Number.isFinite(audio.duration) || audio.duration <= 0) return;
                const bounds = e.currentTarget.getBoundingClientRect();
                audio.currentTime = Math.max(0, Math.min(1, (e.clientX - bounds.left) / bounds.width)) * audio.duration;
              }}
            >
              <img src={assetWaveformUrl(audition.assetId)} alt="" onError={() => setWaveformError(true)} />
            </button>
          )}
          <audio
            ref={audioRef}
            controls
            preload="metadata"
            src={assetMediaUrl(audition.assetId)}
            onError={() => setAuditionError(tr("音频无法播放，请检查素材是否仍可访问或浏览器是否支持此格式。"))}
          />
          {auditionError ? (
            <div className="media-audition__error" role="alert">
              <p>{auditionError}</p>
              <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
                onClick={() => setAuditionAttempt((attempt) => attempt + 1)}>{tr("重试试听")}</button>
            </div>
          ) : null}
        </section>
      ) : null}
      {(domain === "media" || domain === "music") ? <p className="cv-hint" style={{ marginTop: 8 }}>
        {domain === "music"
          ? tr("可将导入音频分类为背景音乐或音效；先试听并查看波形，再拖到独立音轨。")
          : tr("拖到轨道上即可插入，后续片段会顺延（时长识别失败按 2 秒）；拖到下方空白可自动建轨。")}
      </p> : null}
      </div>
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
  if (kind === "image") return tr("图片");
  if (kind === "audio") return tr("音频");
  if (kind === "video") return tr("视频");
  return tr("未知类型");
}
