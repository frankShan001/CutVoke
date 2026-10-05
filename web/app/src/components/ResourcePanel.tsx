/** 资源库面板（J01）：分类 tab / 搜索 / 收藏/最近筛选 / 效果卡片 / 应用到选中片段。
 *
 * 数据全部来自 GET /projects/{id}/resources（工程级：带 favorite/recent 标记），
 * 分类由 category 枚举派生（绝对不写死效果 ID 列表）。普通效果走 effect.add，动画走 effect.setAnimation +
 * 既有刷新机制；请求失败显式 showError，不静默降级。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Search, Star, Loader2, CheckSquare2, Layers, Eye, Play, X } from "lucide-react";
import { Panel } from "./ui";
import { useEditor, showError } from "../store/editor";
import {
  activateResourcePack,
  addResourceRegistry,
  downloadResourceRegistryPackage,
  getResourcePackManagerStatus,
  installResourcePack,
  listResourceRegistries,
  refreshResourceRegistries,
  removeResourceRegistry,
  rollbackResourcePack,
  trustResourcePackPublisher,
  type ResourcePackEntry,
  type ResourcePackManagerStatus,
  type ResourceRegistry,
} from "../lib/api";
import { getLatestState, runCommand } from "../store/actions";
import { addEffectToClip, setFavorite } from "../store/effectEdit";
import { applyAnimation } from "../store/clipEdit";
import { applyTransition } from "../store/clipEdit";
import { rationalToSecs } from "../lib/rational";
import { TRANSITION_DND_TYPE } from "../lib/transitions";
import {
  categoryLabelOf,
  builtinPresetMediaUrl,
  fetchResourcePreview,
  iconForCategory,
  listBuiltinPresets,
  listProjectResources,
  sortCategories,
  type EffectCategory,
  type ProjectResource,
  type BuiltinPresetCatalog,
  type BuiltinPresetSummary,
} from "../lib/effects";

interface Props {
  active: boolean;
  defaultFamily?: string;
}

// 每张封面都走同一渲染接口；串行按需加载，避免滚动时同时启动大量 FFmpeg 进程。
let thumbnailQueue: Promise<void> = Promise.resolve();

function ResourceThumbnail({
  active, projectId, clipId, effectId, name, revision, sample, cache,
}: {
  active: boolean;
  projectId: string;
  clipId: string;
  effectId: string;
  name: string;
  revision: string;
  sample: boolean;
  cache: Map<string, string>;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const key = `${projectId}:${revision}:${clipId}:${effectId}`;

  useEffect(() => {
    if (!active || !holder.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: "80px" });
    observer.observe(holder.current);
    return () => observer.disconnect();
  }, [active]);

  useEffect(() => {
    if (!active || !visible) return;
    const cached = cache.get(key);
    if (cached) {
      setUrl(cached);
      setError(false);
      setLoading(false);
      return;
    }
    setUrl(null);
    setError(false);
    setLoading(true);
    const controller = new AbortController();
    thumbnailQueue = thumbnailQueue.then(async () => {
      if (controller.signal.aborted) return;
      const result = await fetchResourcePreview(projectId, clipId, effectId, controller.signal);
      if (controller.signal.aborted) {
        if (result.kind === "frame") URL.revokeObjectURL(result.url);
        return;
      }
      if (result.kind === "frame") {
        cache.set(key, result.url);
        setUrl(result.url);
        setLoading(false);
      } else {
        setError(true);
        setLoading(false);
      }
    }).catch(() => {
      if (!controller.signal.aborted) {
        setError(true);
        setLoading(false);
      }
    });
    return () => controller.abort();
  }, [active, visible, projectId, clipId, effectId, key, cache, attempt]);

  return (
    <div ref={holder} className={`resource-card__thumbnail${error ? " resource-card__thumbnail--error" : ""}`}>
      {url ? <>
        <img src={url} alt={`${name}在${sample ? "工程示例片段" : "选中片段"}上的预览帧`} />
        <span className="resource-card__thumbnail-cache" title="缩略图已缓存在当前编辑会话">
          已缓存
        </span>
      </> : error ? <div className="resource-card__thumbnail-error" role="status">
        <span>封面生成失败</span>
        <button type="button" className="resource-card__thumbnail-retry"
          aria-label={`重试${name}封面`} onClick={(event) => {
            event.stopPropagation();
            setAttempt((current) => current + 1);
          }}>重试封面</button>
      </div> : <span role="status">
        {loading ? <><Loader2 size={12} className="cv-spin" /> 封面生成中…</> : "滚动到此处生成封面"}
      </span>}
    </div>
  );
}

export function ResourcePanel({ active, defaultFamily = "" }: Props) {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const pid = state.currentId;

  const [resources, setResources] = useState<ProjectResource[]>([]);
  const resourcesProject = useRef<string | null>(null);
  const [builtinPresets, setBuiltinPresets] = useState<BuiltinPresetCatalog | null>(null);
  const [presetLoading, setPresetLoading] = useState(false);
  const [presetLoadError, setPresetLoadError] = useState<string | null>(null);
  const [presetFamily, setPresetFamily] = useState(defaultFamily);
  const [presetSubcategory, setPresetSubcategory] = useState("");
  const [samplePreset, setSamplePreset] = useState<BuiltinPresetSummary | null>(null);
  const [sampleVideoError, setSampleVideoError] = useState(false);
  const [sampleVideoAttempt, setSampleVideoAttempt] = useState(0);
  const sampleCloseButton = useRef<HTMLButtonElement>(null);
  const sampleOpener = useRef<HTMLButtonElement>(null);
  const [favIds, setFavIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<EffectCategory | "">("");
  const [subcategory, setSubcategory] = useState("");
  const [favOnly, setFavOnly] = useState(false);
  const [recOnly, setRecOnly] = useState(false);
  const [applying, setApplying] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ effectId: string; clipId: string; name: string; sample: boolean; url?: string; error?: string; loading: boolean } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const previewRequest = useRef<AbortController | null>(null);
  const previewUrl = useRef<string | null>(null);
  const thumbnailUrls = useRef(new Map<string, string>());
  const categoryTabRefs = useRef(new Map<string, HTMLButtonElement>());
  const [packManager, setPackManager] = useState<ResourcePackManagerStatus | null>(null);
  const [packManagerError, setPackManagerError] = useState<string | null>(null);
  const [packManagerMessage, setPackManagerMessage] = useState<string | null>(null);
  const [packManagerBusy, setPackManagerBusy] = useState(false);
  const [packManagerStatusLoading, setPackManagerStatusLoading] = useState(false);
  const [packManagerStatusError, setPackManagerStatusError] = useState<string | null>(null);
  const [trustTarget, setTrustTarget] = useState<ResourcePackEntry | null>(null);
  const [registries, setRegistries] = useState<ResourceRegistry[]>([]);
  const [registryLoading, setRegistryLoading] = useState(false);
  const [registryLoadError, setRegistryLoadError] = useState<string | null>(null);
  const [registryError, setRegistryError] = useState<string | null>(null);
  const [registryUrl, setRegistryUrl] = useState("");
  const [registryTrustTarget, setRegistryTrustTarget] = useState<ResourceRegistry | null>(null);
  const [trustFingerprint, setTrustFingerprint] = useState("");
  const [trustPublicKeyPem, setTrustPublicKeyPem] = useState("");

  const refreshPackManager = useCallback(async () => {
    setPackManagerStatusLoading(true);
    setPackManagerStatusError(null);
    try {
      setPackManager(await getResourcePackManagerStatus());
      setPackManagerError(null);
    } catch (error) {
      setPackManagerStatusError(error instanceof Error ? error.message : "资源包状态读取失败");
    } finally {
      setPackManagerStatusLoading(false);
    }
  }, []);

  const loadRegistries = useCallback(async () => {
    setRegistryLoading(true);
    setRegistryLoadError(null);
    try {
      const result = await listResourceRegistries();
      setRegistries(result.registries);
    } catch (error) {
      setRegistryLoadError(error instanceof Error ? error.message : "资源目录读取失败");
    } finally {
      setRegistryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (active) {
      void refreshPackManager();
      void loadRegistries();
    }
  }, [active, refreshPackManager, loadRegistries]);

  const selectedClipId = state.selection?.clipId ?? null;
  const selectedTrackLocked = !!state.project?.sequence.tracks.find((track) =>
    track.clips.some((clip) => clip.id === selectedClipId),
  )?.locked;
  const selectedClipReadOnly = selectedTrackLocked || !!state.editLock;

  // 尚未选片段时，使用工程里已有画面做资源封面；用户仍需明确选目标才能应用。
  const sampleTargets = useMemo(() => {
    const tracks = state.project?.sequence.tracks.filter((track) => track.kind === "video") || [];
    const first = tracks.flatMap((track) => track.clips)[0]?.id || null;
    let transition: string | null = null;
    for (const track of tracks) {
      const clips = [...track.clips].sort((a, b) => rationalToSecs(a.timelineStart) - rationalToSecs(b.timelineStart));
      for (let i = 1; i < clips.length; i += 1) {
        if (Math.abs(rationalToSecs(clips[i].timelineStart) - rationalToSecs(clips[i - 1].timelineEnd)) <= 0.001) {
          transition = clips[i].id;
          break;
        }
      }
      if (transition) break;
    }
    return { first, transition };
  }, [state.project]);

  // 与服务端同一语义：静态图片只接受视觉效果；视频片段同时可处理画面与原声。
  // 资源面板先隐藏不相关的卡片，服务端仍会二次校验，保证 Agent 与人工操作一致。
  const selectedTargetTypes = useMemo(() => {
    if (!selectedClipId) return [] as string[];
    const track = state.project?.sequence.tracks.find((item) =>
      item.clips.some((clip) => clip.id === selectedClipId),
    );
    if (!track) return [] as string[];
    if (track.kind === "audio") return ["audio"];
    if (track.kind === "text") return ["text"];
    const clip = track.clips.find((item) => item.id === selectedClipId);
    const source = (clip?.assetRef.sourcePath || "").split("?", 1)[0].toLowerCase();
    if (/\.(avif|bmp|gif|heic|jpe?g|png|tiff?|webp)$/.test(source)) return ["image"];
    return ["video", "audio"];
  }, [selectedClipId, state.project]);

  // 转场不是单片段滤镜：它挂在「后一段」上，并且只对紧邻的前一段有语义。
  // 把约束提前到资源库，避免用户点完才遇到无画面变化或后端错误。
  const transitionTarget = useMemo(() => {
    if (!selectedClipId) return { valid: false, reason: "请先选中转场后的片段", maxDuration: 0 };
    const track = state.project?.sequence.tracks.find((item) =>
      item.clips.some((clip) => clip.id === selectedClipId),
    );
    if (!track || track.kind !== "video") return { valid: false, reason: "转场只能用于视频轨道", maxDuration: 0 };
    const clips = [...track.clips].sort(
      (a, b) => rationalToSecs(a.timelineStart) - rationalToSecs(b.timelineStart),
    );
    const index = clips.findIndex((clip) => clip.id === selectedClipId);
    if (index <= 0) return { valid: false, reason: "首个片段前没有可连接的片段", maxDuration: 0 };
    const previous = clips[index - 1];
    const current = clips[index];
    const gap = rationalToSecs(current.timelineStart) - rationalToSecs(previous.timelineEnd);
    if (Math.abs(gap) > 0.001) return { valid: false, reason: "转场两侧需要首尾相接；请先关闭中间空隙", maxDuration: 0 };
    const previousDuration = rationalToSecs(previous.timelineEnd) - rationalToSecs(previous.timelineStart);
    const currentDuration = rationalToSecs(current.timelineEnd) - rationalToSecs(current.timelineStart);
    const maxDuration = Math.min(5, previousDuration / 2, currentDuration / 2);
    if (maxDuration < 0.1) return { valid: false, reason: "相邻片段过短，无法形成转场", maxDuration };
    return { valid: true, reason: `将连接前一段与当前片段`, maxDuration };
  }, [selectedClipId, state.project]);

  const load = useCallback(
    async () => {
      if (!pid) {
        setResources([]);
        setFavIds([]);
        setLoadError(null);
        return;
      }
      setLoading(true);
      setLoadError(null);
      try {
        // 保留完整目录，搜索与两级分类在本地筛选；切分类不会让其它分类消失。
        const res = await listProjectResources(pid);
        setResources(res.effects);
        resourcesProject.current = pid;
        setFavIds(res.favorites);
      } catch (err) {
        setResources([]);
        setFavIds([]);
        setLoadError(err instanceof Error && err.message ? err.message : "请检查服务连接后重试。");
        showError(dispatch, err);
      } finally {
        setLoading(false);
      }
    },
    [pid, dispatch],
  );

  const loadBuiltinPresets = useCallback(async () => {
    if (!pid) {
      setBuiltinPresets(null);
      setPresetLoadError(null);
      return;
    }
    setPresetLoading(true);
    setPresetLoadError(null);
    try {
      setBuiltinPresets(await listBuiltinPresets());
    } catch (error) {
      setBuiltinPresets(null);
      setPresetLoadError(error instanceof Error && error.message
        ? error.message : "内置预设审核状态暂不可用。");
    } finally {
      setPresetLoading(false);
    }
  }, [pid]);

  // 面板激活 / 工程切换 / 状态变化（应用效果后工程 revision 变化）时刷新
  useEffect(() => {
    if (active && pid) {
      void load();
      void loadBuiltinPresets();
    }
  }, [active, pid, state.revision, state.project?.revision, load, loadBuiltinPresets]);

  const refresh = () => void load();

  useEffect(() => {
    previewRequest.current?.abort();
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
    setPreview(null);
    return () => {
      previewRequest.current?.abort();
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
      previewUrl.current = null;
    };
  }, [pid, selectedClipId, state.project?.revision]);

  useEffect(() => {
    const urls = thumbnailUrls.current;
    return () => {
      for (const url of urls.values()) URL.revokeObjectURL(url);
      urls.clear();
    };
  }, [pid, selectedClipId, state.project?.revision]);

  useEffect(() => {
    if (!samplePreset) return;
    sampleCloseButton.current?.focus();
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSamplePreset(null);
        requestAnimationFrame(() => sampleOpener.current?.focus());
      }
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [samplePreset]);

  useEffect(() => {
    setSampleVideoError(false);
    setSampleVideoAttempt(0);
  }, [samplePreset?.presetId]);

  const available = useMemo(
    () => resources.filter((e) => selectedTargetTypes.length === 0 ||
      e.appliesTo.some((type) => selectedTargetTypes.includes(type))),
    [resources, selectedTargetTypes],
  );
  const categories = useMemo(() => sortCategories(available.map((e) => e.browseCategory)), [available]);
  const categoryTabOrder = useMemo(() => ["", ...categories], [categories]);
  const subcategories = useMemo(() => {
    if (!cat) return [];
    return [...new Set(available.filter((e) => e.browseCategory === cat).map((e) => e.subcategory || "基础"))].sort();
  }, [available, cat]);

  useEffect(() => {
    if (cat && !categories.includes(cat)) {
      setCat("");
      setSubcategory("");
    }
  }, [cat, categories]);

  const selectCategory = (category: string) => {
    setCat(category as EffectCategory | "");
    setSubcategory("");
  };

  const onCategoryTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (index + 1) % categoryTabOrder.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (index - 1 + categoryTabOrder.length) % categoryTabOrder.length;
    } else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = categoryTabOrder.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = categoryTabOrder[nextIndex];
    selectCategory(next);
    requestAnimationFrame(() => categoryTabRefs.current.get(next)?.focus());
  };

  const filtered = useMemo(
    () => available.filter((e) => {
      if ((favOnly && !e.favorite) || (recOnly && !e.recent)) return false;
      if (cat && e.browseCategory !== cat) return false;
      if (subcategory && (e.subcategory || "基础") !== subcategory) return false;
      const needle = q.trim().toLocaleLowerCase();
      return !needle || [e.name, e.description, e.effectId, categoryLabelOf(e.browseCategory),
        e.subcategory, ...e.keywords]
        .some((part) => part.toLocaleLowerCase().includes(needle));
    }),
    [available, favOnly, recOnly, cat, subcategory, q],
  );

  const catalogPresets = useMemo(() =>
    (builtinPresets?.presets || []).filter((preset) => preset.status !== "retired"),
  [builtinPresets]);
  const presetFamilies = useMemo(() =>
    [...new Set(catalogPresets.map((preset) => preset.family))], [catalogPresets]);
  const presetSubcategories = useMemo(() =>
    [...new Set(catalogPresets.filter((preset) => !presetFamily || preset.family === presetFamily)
      .map((preset) => preset.subcategory))], [catalogPresets, presetFamily]);
  const visiblePresets = useMemo(() => catalogPresets.filter((preset) => {
    if (presetFamily && preset.family !== presetFamily) return false;
    if (presetSubcategory && preset.subcategory !== presetSubcategory) return false;
    const needle = q.trim().toLocaleLowerCase();
    return !needle || [preset.name, preset.presetId, preset.subcategory, preset.effectId]
      .some((part) => part.toLocaleLowerCase().includes(needle));
  }), [catalogPresets, presetFamily, presetSubcategory, q]);

  const showPreview = async (effect: ProjectResource, clipId: string) => {
    if (!pid) return;
    const sample = clipId !== selectedClipId;
    previewRequest.current?.abort();
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
    const controller = new AbortController();
    previewRequest.current = controller;
    setPreview({ effectId: effect.effectId, clipId, name: effect.name, sample, loading: true });
    try {
      const result = await fetchResourcePreview(pid, clipId, effect.effectId, controller.signal);
      if (controller.signal.aborted) {
        if (result.kind === "frame") URL.revokeObjectURL(result.url);
        return;
      }
      if (result.kind === "frame") {
        previewUrl.current = result.url;
        setPreview({ effectId: effect.effectId, clipId, name: effect.name, sample, url: result.url, loading: false });
      } else {
        setPreview({ effectId: effect.effectId, clipId, name: effect.name, sample,
          error: result.kind === "empty" ? "所选片段在试用时间没有画面" : result.message,
          loading: false });
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setPreview({ effectId: effect.effectId, clipId, name: effect.name, sample,
        error: error instanceof Error ? error.message : "预览发生意外错误",
        loading: false });
    }
  };

  const selectionContext = useMemo(() => {
    if (!selectedClipId) return "可先浏览工程示例封面；应用前请选中目标片段";
    if (selectedTargetTypes.includes("image")) return "图片片段：仅显示视觉效果";
    if (selectedTargetTypes.includes("text")) return "文字片段：仅显示文字效果";
    if (selectedTargetTypes.includes("audio") && !selectedTargetTypes.includes("video")) {
      return "音频片段：仅显示音频效果";
    }
    return "视频片段：显示画面与声音效果";
  }, [selectedClipId, selectedTargetTypes]);

  const applyToSelected = async (effect: ProjectResource) => {
    if (!selectedClipId || selectedClipReadOnly) return;
    if (effect.category === "transition" && !transitionTarget.valid) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: `无法应用转场：${transitionTarget.reason}` });
      return;
    }
    if (applying) return;
    setApplying(effect.effectId);
    const st = getLatestState() || state;
    const res = effect.category === "transition"
      ? await applyTransition(dispatch, st, {
          clipId: selectedClipId,
          effectId: effect.effectId,
          duration: Math.round(Math.min(
            typeof effect.defaults.duration === "number" ? effect.defaults.duration : 1,
            transitionTarget.maxDuration,
          ) * 1000) / 1000,
        })
      : effect.category === "animation"
      ? await applyAnimation(dispatch, st, {
          clipId: selectedClipId,
          animationId: effect.effectId,
          duration: typeof effect.defaults.duration === "number" ? effect.defaults.duration : 1,
          params: effect.defaults,
        })
      : await addEffectToClip(dispatch, st, {
          clipId: selectedClipId,
          effectId: effect.effectId,
          params: undefined, // 后端按默认值补齐
        });
    setApplying(null);
    if (res.ok) {
      dispatch({ type: "STATUS_SET", severity: "ok", text: `已应用「${effect.name}」到选中片段` });
    }
  };

  const applyPreset = async (preset: BuiltinPresetSummary) => {
    if (!selectedClipId || selectedClipReadOnly || applying) return;
    if (preset.downloadState !== "bundled" || !preset.mediaAvailable) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "此预设资源尚不可用" });
      return;
    }
    if (preset.family === "transition" && !transitionTarget.valid) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: `无法应用转场：${transitionTarget.reason}` });
      return;
    }
    if (!preset.appliesTo.some((type) => selectedTargetTypes.includes(type))) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "此预设不适用于当前片段" });
      return;
    }
    setApplying(preset.presetId);
    const latest = getLatestState() || state;
    const payload: Record<string, unknown> = {
      clipId: selectedClipId, presetId: preset.presetId,
    };
    if (preset.family === "transition") {
      payload.duration = Math.round(Math.min(
        typeof preset.params.duration === "number" ? preset.params.duration : 1,
        transitionTarget.maxDuration,
      ) * 1000) / 1000;
    }
    const result = await runCommand(dispatch, latest, "builtinPreset.apply", payload);
    setApplying(null);
    if (result.ok) dispatch({ type: "STATUS_SET", severity: "ok", text: `已应用预设「${preset.name}」` });
  };

  const toggleFav = async (effect: ProjectResource) => {
    const st = getLatestState() || state;
    await setFavorite(dispatch, st, {
      effectId: effect.effectId,
      favorite: !effect.favorite,
    });
    refresh();
  };

  const importResourcePack = async (file: File | null) => {
    if (!file) return;
    setPackManagerBusy(true);
    setPackManagerError(null);
    setPackManagerMessage(null);
    try {
      const installed = await installResourcePack(file);
      await refreshPackManager();
      setPackManagerMessage(`已安装 v${installed.version}，可在下方列表中启用。`);
    } catch (error) {
      setPackManagerError(error instanceof Error ? error.message : "资源包安装失败");
    } finally {
      setPackManagerBusy(false);
    }
  };

  const activatePack = async (packId: string, version: string) => {
    setPackManagerBusy(true);
    setPackManagerError(null);
    setPackManagerMessage(null);
    try {
      setPackManager(await activateResourcePack(packId, version));
      setPackManagerMessage("资源包已启用；刷新页面后加载新的预设和贴纸目录。");
    } catch (error) {
      setPackManagerError(error instanceof Error ? error.message : "资源包启用失败");
    } finally {
      setPackManagerBusy(false);
    }
  };

  const rollbackPack = async () => {
    setPackManagerBusy(true);
    setPackManagerError(null);
    setPackManagerMessage(null);
    try {
      setPackManager(await rollbackResourcePack());
      setPackManagerMessage("已回滚资源包；刷新页面后加载回滚版本的目录。");
    } catch (error) {
      setPackManagerError(error instanceof Error ? error.message : "资源包回滚失败");
    } finally {
      setPackManagerBusy(false);
    }
  };

  const addRegistry = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const url = registryUrl.trim();
    if (!url) return;
    setPackManagerBusy(true);
    setRegistryError(null);
    setPackManagerMessage(null);
    try {
      const added = await addResourceRegistry(url);
      setRegistries((current) => [...current.filter((item) => item.url !== added.url), added]);
      setRegistryUrl("");
      setPackManagerMessage(`已添加资源目录「${added.displayName || added.url}」。`);
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : "添加资源目录失败");
    } finally {
      setPackManagerBusy(false);
    }
  };

  const refreshRegistries = async (url?: string) => {
    setPackManagerBusy(true);
    setRegistryError(null);
    try {
      const result = await refreshResourceRegistries(url);
      if (url) {
        setRegistries((current) => current.map((item) =>
          item.url === url ? result.registries[0] ?? item : item));
      } else {
        setRegistries(result.registries);
      }
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : "刷新资源目录失败");
    } finally {
      setPackManagerBusy(false);
    }
  };

  const removeRegistry = async (url: string) => {
    setPackManagerBusy(true);
    setRegistryError(null);
    try {
      const result = await removeResourceRegistry(url);
      setRegistries(result.registries);
      setPackManagerMessage("已移除资源目录；已安装的包不会被删除。");
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : "移除资源目录失败");
    } finally {
      setPackManagerBusy(false);
    }
  };

  const downloadRegistryPackage = async (registry: ResourceRegistry, packId: string, version: string) => {
    setPackManagerBusy(true);
    setRegistryError(null);
    setPackManagerMessage("正在下载并校验；如果连接中断，再点一次会从已下载位置续传。");
    try {
      const result = await downloadResourceRegistryPackage(registry.url, packId, version);
      await Promise.all([refreshPackManager(), loadRegistries()]);
      setPackManagerMessage(`已下载并安装「${result.installed.packId}」v${result.installed.version}；可在已安装列表中手动启用。`);
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : "下载资源包失败；可以重试并续传。");
      setPackManagerMessage(null);
    } finally {
      setPackManagerBusy(false);
    }
  };

  const trustRegistry = async () => {
    if (!registryTrustTarget?.publisherId || !trustPublicKeyPem || !trustFingerprint.trim()) return;
    setPackManagerBusy(true);
    setRegistryError(null);
    setPackManagerMessage(null);
    try {
      const status = await trustResourcePackPublisher(
        registryTrustTarget.publisherId, trustPublicKeyPem, trustFingerprint.trim(),
      );
      setPackManager(status);
      setRegistryTrustTarget(null);
      const refreshed = await refreshResourceRegistries();
      setRegistries(refreshed.registries);
      setPackManagerMessage(`发布者「${registryTrustTarget.publisherId}」的密钥已核对并加入本机信任列表。`);
    } catch (error) {
      setRegistryError(error instanceof Error ? error.message : "发布者密钥验证失败");
    } finally {
      setPackManagerBusy(false);
    }
  };

  const beginTrustPublisher = (item: ResourcePackEntry) => {
    setRegistryTrustTarget(null);
    setTrustTarget(item);
    setTrustFingerprint("");
    setTrustPublicKeyPem("");
    setPackManagerError(null);
    setPackManagerMessage(null);
  };

  const beginTrustRegistry = (registry: ResourceRegistry) => {
    setTrustTarget(null);
    setRegistryTrustTarget(registry);
    setTrustFingerprint("");
    setTrustPublicKeyPem("");
    setRegistryError(null);
    setPackManagerMessage(null);
  };

  const trustPublisher = async () => {
    if (!trustTarget || !trustPublicKeyPem || !trustFingerprint.trim()) return;
    setPackManagerBusy(true);
    setPackManagerError(null);
    setPackManagerMessage(null);
    try {
      const status = await trustResourcePackPublisher(
        trustTarget.publisherId, trustPublicKeyPem, trustFingerprint.trim(),
      );
      setPackManager(status);
      setTrustTarget(null);
      setPackManagerMessage(`发布者「${trustTarget.publisherId}」的密钥已核对并加入本机信任列表。`);
    } catch (error) {
      setPackManagerError(error instanceof Error ? error.message : "发布者密钥验证失败");
    } finally {
      setPackManagerBusy(false);
    }
  };

  return (
    <Panel title="资源库" subtitle="按类型与细分类浏览真实效果">
      {selectedClipReadOnly ? (
        <p className="cv-hint" role="status">
          {state.editLock ? "Agent 正在编辑，效果暂不可应用。" : "所选片段所在轨道已锁定，解锁后可应用效果。"}
        </p>
      ) : null}
      <p className="resource-context" role="status">
        <span>{selectionContext}</span>
        <span>{filtered.length} / {available.length} 个效果</span>
      </p>
      {builtinPresets ? (
        <p className="cv-hint resource-catalog-status" title="效果规格、候选预设与经完整工作流验收的预设分开计数；资源包清单含版本、许可证与媒体 SHA-256">
          已注册效果 {resources.length} · 内置候选预设 {builtinPresets.candidateCount} · 合格预设 {builtinPresets.qualifiedCount}
          {builtinPresets.resourcePack ? ` · 资源包 v${builtinPresets.resourcePack.version} · ${builtinPresets.resourcePack.offlineAvailable ? "离线可用" : `异常 ${builtinPresets.resourcePack.missingFiles.length + (builtinPresets.resourcePack.invalidFiles?.length ?? 0)} 个文件`}` : ""}
        </p>
      ) : presetLoading ? (
        <p className="cv-hint" role="status">正在读取内置预设审核状态；效果清单可继续浏览。</p>
      ) : presetLoadError ? (
        <p className="resource-pack-manager__error" role="alert">
          <span>内置预设审核状态读取失败：{presetLoadError}；效果清单仍可浏览。</span>
          <button type="button" className="cv-button cv-button--small"
            disabled={presetLoading} onClick={() => void loadBuiltinPresets()}>重试内置预设</button>
        </p>
      ) : null}
      <details className="resource-pack-manager">
        <summary>离线资源包管理{packManager ? ` · 当前 v${packManager.active.version}` : ""}</summary>
        <div className="resource-pack-manager__body">
          <label className="resource-pack-manager__import">
            安装离线 ZIP
            <input type="file" accept=".zip,application/zip" aria-label="安装离线资源包 ZIP"
              disabled={packManagerBusy}
              onChange={(event) => {
                const file = event.currentTarget.files?.[0] ?? null;
                void importResourcePack(file);
                event.currentTarget.value = "";
              }} />
          </label>
          {packManagerBusy ? <span role="status">正在处理资源目录或资源包…</span> : null}
          {packManagerError ? <p className="resource-pack-manager__error" role="alert">{packManagerError}</p> : null}
          {packManagerStatusLoading ? <p role="status">正在读取本机资源包状态…</p> : null}
          {packManagerStatusError ? (
            <p className="resource-pack-manager__error" role="alert">
              <span>资源包状态读取失败：{packManagerStatusError}</span>
              <button type="button" className="cv-button cv-button--small"
                disabled={packManagerStatusLoading}
                onClick={() => void refreshPackManager()}>重试资源包状态</button>
            </p>
          ) : null}
          {packManagerMessage ? <p className="resource-pack-manager__message" role="status">{packManagerMessage}</p> : null}
          <section className="resource-registry" aria-label="在线资源目录">
            <div className="resource-registry__heading">
              <strong>在线资源目录</strong>
              <button type="button" className="cv-button cv-button--small"
                disabled={packManagerBusy || registryLoading} onClick={() => void refreshRegistries()}>
                刷新全部
              </button>
            </div>
            <form className="resource-registry__add" onSubmit={(event) => void addRegistry(event)}>
              <label htmlFor="resource-registry-url">签名目录 HTTPS 地址</label>
              <div>
                <input id="resource-registry-url" type="url" value={registryUrl}
                  onChange={(event) => setRegistryUrl(event.currentTarget.value)}
                  placeholder="https://example.com/resources.json" required />
                <button type="submit" className="cv-button cv-button--small"
                  disabled={packManagerBusy || !registryUrl.trim()}>添加</button>
              </div>
              <small>目录与资源包都需由发布者签名并校验 SHA-256；开发用本机回环地址也可使用 HTTP。</small>
            </form>
    {registryLoading ? <p className="cv-hint" role="status">正在读取在线资源目录…</p> : null}
            {registryLoadError ? (
              <p className="resource-pack-manager__error" role="alert">
                <span>在线资源目录读取失败：{registryLoadError}</span>
                <button type="button" className="cv-button cv-button--small"
                  disabled={registryLoading}
                  onClick={() => void loadRegistries()}>重试资源目录</button>
              </p>
            ) : null}
            {registryError ? <p className="resource-pack-manager__error" role="alert">
              在线资源目录操作失败：{registryError}
            </p> : null}
            {!registryLoading && !registryLoadError && registries.length === 0
              ? <p className="cv-hint">尚未添加在线资源目录。</p> : null}
            <ul className="resource-registry__list" aria-label="在线资源目录列表">
              {registries.map((registry) => (
                <li key={registry.url}>
                  <div className="resource-registry__source">
                    <strong>{registry.displayName || registry.registryId || registry.url}</strong>
                    <small>{registry.signatureStatus === "verified"
                      ? `目录签名已验证 · ${registry.publisherId}`
                      : registry.signatureStatus === "unknown_publisher"
                      ? `目录发布者未受信任 · ${registry.publisherId}`
                      : "目录暂不可用，保留上次成功读取内容"}</small>
                    {registry.fingerprint ? <code>SHA-256：{registry.fingerprint}</code> : null}
                    {registry.lastError ? <small className="resource-registry__error">刷新失败：{registry.lastError}</small> : null}
                  </div>
                  <div className="resource-registry__actions">
                    {registry.signatureStatus === "unknown_publisher" ? <button type="button"
                      className="cv-button cv-button--small" disabled={packManagerBusy}
                      onClick={() => beginTrustRegistry(registry)}>核对并信任目录发布者</button> : null}
                    <button type="button" className="cv-button cv-button--small"
                      disabled={packManagerBusy} onClick={() => void refreshRegistries(registry.url)}>刷新</button>
                    <button type="button" className="cv-button cv-button--small"
                      disabled={packManagerBusy} onClick={() => void removeRegistry(registry.url)}>移除</button>
                  </div>
                  {registryTrustTarget?.url === registry.url ? (
                    <form className="resource-pack-manager__trust" onSubmit={(event) => {
                      event.preventDefault();
                      void trustRegistry();
                    }}>
                      <strong>核对 {registry.publisherId} 的目录签名密钥</strong>
                      <span>从可信渠道确认此 SHA-256 指纹，再导入发布者提供的 Ed25519 公钥。</span>
                      <code>{registry.fingerprint || registry.keyId}</code>
                      <label>可信渠道给出的 SHA-256 指纹
                        <input type="text" value={trustFingerprint}
                          onChange={(event) => setTrustFingerprint(event.currentTarget.value)}
                          autoComplete="off" spellCheck={false} required />
                      </label>
                      <label>Ed25519 公钥 PEM 文件
                        <input type="file" accept=".pem,.pub,text/plain" required
                          onChange={(event) => {
                            const file = event.currentTarget.files?.[0];
                            setTrustPublicKeyPem("");
                            if (file) void file.text().then(setTrustPublicKeyPem).catch(() =>
                              setRegistryError("公钥文件读取失败"));
                          }} />
                      </label>
                      <div>
                        <button type="submit" className="cv-button cv-button--small"
                          disabled={packManagerBusy || !trustFingerprint.trim() || !trustPublicKeyPem}>
                          {packManagerBusy ? "验证中…" : "验证指纹并信任目录发布者"}
                        </button>
                        <button type="button" className="cv-button cv-button--small"
                          onClick={() => setRegistryTrustTarget(null)}>取消</button>
                      </div>
                    </form>
                  ) : null}
                  {(registry.packages?.length ?? 0) > 0 ? (
                    <ul className="resource-registry__packages" aria-label={`${registry.displayName || "资源目录"}中的资源包`}>
                      {registry.packages?.map((item) => (
                        <li key={`${item.packId}:${item.version}`}>
                          <span><strong>{item.name}</strong> v{item.version}
                            <small>{item.packId} · {(item.sizeBytes / (1024 * 1024)).toFixed(1)} MB · SHA-256 已登记</small>
                          </span>
                          <button type="button" className="cv-button cv-button--small"
                            disabled={packManagerBusy}
                            onClick={() => void downloadRegistryPackage(registry, item.packId, item.version)}>
                            {packManagerBusy ? "处理中…" : "下载并安装"}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
          {packManager ? <>
            <ul className="resource-pack-manager__list" aria-label="已安装资源包版本">
              {packManager.installed.map((item) => (
                <li key={`${item.packId}:${item.version}`}>
                  <span>
                    <strong>{item.builtin ? "内置资源" : item.packId}</strong> v{item.version}
                    <small>{item.presetCount} 个预设 · {item.stickerCount} 个贴纸 · {item.fileCount} 个文件
                      {!item.offlineAvailable ? " · 校验异常" : ""}
                      {item.provenanceCoverage ? <><br /><span title="此处只统计资源清单中是否填写许可证与来源字段，不表示法律审核或授权已核实。">授权元数据：许可证 {item.provenanceCoverage.licenseDeclaredCount}/{item.provenanceCoverage.resourceCount} · 来源 {item.provenanceCoverage.sourceDeclaredCount}/{item.provenanceCoverage.resourceCount} · 字段待补 {item.provenanceCoverage.incompleteCount}</span></> : null}
                      <br />{item.signatureStatus === "bundled" ? "应用内置资源" :
                        item.signatureStatus === "verified" ? `发布者已验证 · ${item.publisherId}` :
                        item.signatureStatus === "unknown_publisher" ? `发布者未受信任 · ${item.publisherId}` : "未签名 · 发布者未验证"}
                      {item.signatureStatus === "unknown_publisher" ? <><br />SHA-256 指纹：{item.fingerprint || item.keyId}</> : null}
                    </small>
                  </span>
                  {item.signatureStatus === "unknown_publisher" ? (
                    <button type="button" className="cv-button cv-button--small"
                      disabled={packManagerBusy}
                      onClick={() => beginTrustPublisher(item)}>核对并信任</button>
                  ) : null}
                  <button type="button" className="cv-button cv-button--small"
                    disabled={packManagerBusy || item.active || !item.offlineAvailable}
                    onClick={() => void activatePack(item.packId, item.version)}>
                    {item.active ? "当前启用" :
                      item.signatureStatus === "unsigned" || item.signatureStatus === "unknown_publisher" ? "启用未验证包" : "启用"}
                  </button>
                  {trustTarget?.keyId === item.keyId ? (
                    <form className="resource-pack-manager__trust" onSubmit={(event) => {
                      event.preventDefault();
                      void trustPublisher();
                    }}>
                      <strong>核对 {item.publisherId} 的发布者密钥</strong>
                      <span>先从可信渠道确认该 SHA-256 指纹，再填写并导入发布者提供的 Ed25519 公钥 PEM。</span>
                      <code>{item.fingerprint || item.keyId}</code>
                      <label>可信渠道给出的 SHA-256 指纹
                        <input type="text" value={trustFingerprint}
                          onChange={(event) => setTrustFingerprint(event.currentTarget.value)}
                          autoComplete="off" spellCheck={false} required />
                      </label>
                      <label>Ed25519 公钥 PEM 文件
                        <input type="file" accept=".pem,.pub,text/plain" required
                          onChange={(event) => {
                            const file = event.currentTarget.files?.[0];
                            setTrustPublicKeyPem("");
                            if (file) void file.text().then(setTrustPublicKeyPem).catch(() =>
                              setPackManagerError("公钥文件读取失败"));
                          }} />
                      </label>
                      <div>
                        <button type="submit" className="cv-button cv-button--small"
                          disabled={packManagerBusy || !trustFingerprint.trim() || !trustPublicKeyPem}>
                          {packManagerBusy ? "验证中…" : "验证指纹并信任"}
                        </button>
                        <button type="button" className="cv-button cv-button--small"
                          disabled={packManagerBusy}
                          onClick={() => setTrustTarget(null)}>取消</button>
                      </div>
                    </form>
                  ) : null}
                </li>
              ))}
            </ul>
            <button type="button" className="cv-button cv-button--small"
              disabled={packManagerBusy || !packManager.canRollback}
              onClick={() => void rollbackPack()}>回滚上一版本</button>
          </> : null}
        </div>
      </details>
      <div className="media-filter">
        <label className="media-filter__search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索预设、效果或分类…"
            aria-label="搜索预设和效果"
          />
        </label>
      </div>
      {catalogPresets.length > 0 ? (
        <section className="resource-preset-library" aria-label="内置预设库">
          <div className="resource-preset-library__head">
            <strong>内置预设 · 动态样片</strong>
            <span>{visiblePresets.length} / {catalogPresets.length} 项</span>
          </div>
          <p className="cv-hint">预设卡按稳定预设 ID 管理；候选项可试用，完整视觉与工程审核前不计入合格数量。</p>
          {presetFamily === "personFx" ? (
            <p className="cv-hint" role="note">人物特效适用于人物抠像生成并保留 Alpha 通道的视频层。先完成人物抠像，再选中时间线上生成的人物层。</p>
          ) : null}
          <div className="resource-subcategories resource-preset-families" role="group" aria-label="预设类型">
            <button type="button" className={`resource-subcategories__btn${!presetFamily ? " resource-subcategories__btn--active" : ""}`}
              aria-pressed={!presetFamily} onClick={() => { setPresetFamily(""); setPresetSubcategory(""); }}>全部</button>
            {presetFamilies.map((family) => (
              <button key={family} type="button"
                className={`resource-subcategories__btn${presetFamily === family ? " resource-subcategories__btn--active" : ""}`}
                aria-pressed={presetFamily === family}
                onClick={() => { setPresetFamily(family); setPresetSubcategory(""); }}>
                {family === "sticker" ? "贴纸" : categoryLabelOf(family as EffectCategory)}
                <span>{catalogPresets.filter((preset) => preset.family === family).length}</span>
              </button>
            ))}
          </div>
          {presetSubcategories.length > 1 ? (
            <div className="resource-subcategories" role="group" aria-label="预设细分类">
              <button type="button" className={`resource-subcategories__btn${!presetSubcategory ? " resource-subcategories__btn--active" : ""}`}
                aria-pressed={!presetSubcategory} onClick={() => setPresetSubcategory("")}>全部</button>
              {presetSubcategories.map((subcategory) => (
                <button key={subcategory} type="button"
                  className={`resource-subcategories__btn${presetSubcategory === subcategory ? " resource-subcategories__btn--active" : ""}`}
                  aria-pressed={presetSubcategory === subcategory} onClick={() => setPresetSubcategory(subcategory)}>
                  {subcategory}
                </button>
              ))}
            </div>
          ) : null}
          {visiblePresets.length ? (
            <div className="resource-grid resource-preset-grid">
              {visiblePresets.map((preset) => {
                const isTransition = preset.family === "transition";
                const applicable = preset.appliesTo.some((type) => selectedTargetTypes.includes(type));
                const availableMedia = preset.downloadState === "bundled" && preset.mediaAvailable;
                const disabled = !selectedClipId || selectedClipReadOnly || !applicable ||
                  !availableMedia || (isTransition && !transitionTarget.valid);
                return (
                  <div key={preset.presetId} className="resource-card resource-preset-card"
                    draggable={isTransition && availableMedia && !state.editLock}
                    onDragStart={(event) => {
                      if (!isTransition || !availableMedia || state.editLock) return;
                      event.dataTransfer.effectAllowed = "copy";
                      event.dataTransfer.setData(TRANSITION_DND_TYPE, JSON.stringify({
                        effectId: preset.effectId,
                        duration: typeof preset.params.duration === "number" ? preset.params.duration : 1,
                      }));
                    }}>
                    {availableMedia ? (
                      <button type="button" className="resource-preset-card__preview"
                        aria-label={`播放${preset.name}动态样片`}
                        onClick={(event) => {
                          sampleOpener.current = event.currentTarget;
                          setSampleVideoError(false);
                          setSampleVideoAttempt(0);
                          setSamplePreset(preset);
                        }}>
                        <img loading="lazy" src={builtinPresetMediaUrl(preset.presetId, "cover")} alt="" />
                        <span className="resource-preset-card__play" aria-hidden="true"><Play size={18} fill="currentColor" /></span>
                      </button>
                    ) : <span className="resource-card__thumbnail">
                      {preset.downloadState === "available" ? "待下载" : "样片不可用"}
                    </span>}
                    <div className="resource-card__head">
                      <strong className="resource-card__name" title={preset.presetId}>{preset.name}</strong>
                      <span className="resource-card__tag">
                        {!availableMedia ? preset.downloadState === "available" ? "待下载" : "不可用" :
                          preset.qualified ? "合格" : "候选"}
                      </span>
                    </div>
                    <span className="resource-card__group"
                      title={`${preset.license} · 来源：${preset.source || "未记录"} · v${preset.version}`}>
                      {preset.subcategory} · v{preset.version}
                    </span>
                    <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary resource-card__apply"
                      disabled={disabled || applying === preset.presetId}
                      title={!availableMedia ? "资源包未下载或样片文件缺失" :
                        isTransition && !transitionTarget.valid ? transitionTarget.reason :
                        !selectedClipId ? "请先选中片段；样片仍可播放" : !applicable ? "不适用于当前片段" : "应用预设参数"}
                      onClick={() => void applyPreset(preset)}>
                      {applying === preset.presetId ? <Loader2 size={12} className="cv-spin" /> : <CheckSquare2 size={12} />}
                      应用
                    </button>
                  </div>
                );
              })}
            </div>
          ) : <p className="cv-empty">该预设分类暂无匹配内容。</p>}
        </section>
      ) : null}
      {samplePreset ? (
        <div className="resource-sample-overlay"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setSamplePreset(null);
              requestAnimationFrame(() => sampleOpener.current?.focus());
            }
          }}>
          <section className="resource-sample-dialog" role="dialog" aria-modal="true"
            aria-label={`${samplePreset.name}动态样片`}
            onKeyDown={(event) => {
              if (event.key !== "Tab") return;
              const video = event.currentTarget.querySelector("video");
              if (event.shiftKey && document.activeElement === sampleCloseButton.current) {
                event.preventDefault();
                video?.focus();
              } else if (!event.shiftKey && document.activeElement === video) {
                event.preventDefault();
                sampleCloseButton.current?.focus();
              }
            }}>
            <div className="resource-sample-dialog__head">
              <div>
                <strong>{samplePreset.name}</strong>
                <span>{categoryLabelOf(samplePreset.family as EffectCategory)} · {samplePreset.subcategory} · {samplePreset.license} · v{samplePreset.version}</span>
                <small className="cv-hint" title={samplePreset.source || "来源未记录"}>
                  来源：{samplePreset.source || "未记录"}
                </small>
              </div>
              <button ref={sampleCloseButton} type="button"
                aria-label="关闭动态样片"
                onClick={() => {
                  setSamplePreset(null);
                  requestAnimationFrame(() => sampleOpener.current?.focus());
                }}><X size={18} /></button>
            </div>
            <video key={`${samplePreset.presetId}-${sampleVideoAttempt}`}
              controls autoPlay muted loop playsInline tabIndex={0} preload="metadata"
              poster={builtinPresetMediaUrl(samplePreset.presetId, "cover")}
              src={builtinPresetMediaUrl(samplePreset.presetId, "preview")}
              aria-label={`${samplePreset.name}动态样片视频`}
              onError={() => setSampleVideoError(true)}
              onLoadedData={() => setSampleVideoError(false)} />
            {sampleVideoError ? (
              <div className="resource-preview__failure" role="alert">
                <span>动态样片加载失败，请检查本地预设资源后重试。</span>
                <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
                  aria-label="重试动态样片"
                  onClick={() => {
                    setSampleVideoError(false);
                    setSampleVideoAttempt((attempt) => attempt + 1);
                  }}>
                  重试
                </button>
              </div>
            ) : null}
            <p>示例样片展示效果变化；应用后可在时间线继续调整。</p>
          </section>
        </div>
      ) : null}
      <h3 className="resource-operator-heading">效果规格 · 手工调参</h3>

      {/* 分类 tab：由 category 枚举派生 */}
      <div className="resource-tabs" role="tablist" aria-label="效果分类">
        <button
          type="button"
          role="tab"
          ref={(element) => {
            if (element) categoryTabRefs.current.set("", element);
            else categoryTabRefs.current.delete("");
          }}
          aria-selected={cat === ""}
          tabIndex={cat === "" ? 0 : -1}
          className={`resource-tabs__btn ${cat === "" ? "resource-tabs__btn--active" : ""}`}
          onClick={() => selectCategory("")}
          onKeyDown={(event) => onCategoryTabKeyDown(event, 0)}
        >
          全部
        </button>
        {categories.map((c) => (
          <button
            key={c}
            type="button"
            role="tab"
            ref={(element) => {
              if (element) categoryTabRefs.current.set(c, element);
              else categoryTabRefs.current.delete(c);
            }}
            aria-selected={cat === c}
            tabIndex={cat === c ? 0 : -1}
            className={`resource-tabs__btn ${cat === c ? "resource-tabs__btn--active" : ""}`}
            onClick={() => selectCategory(c)}
            onKeyDown={(event) => onCategoryTabKeyDown(event, categoryTabOrder.indexOf(c))}
          >
            {categoryLabelOf(c)} <span className="resource-tabs__count">{available.filter((e) => e.browseCategory === c).length}</span>
          </button>
        ))}
      </div>

      {cat && subcategories.length > 1 ? (
        <div className="resource-subcategories" role="group" aria-label={`${categoryLabelOf(cat)}细分类`}>
          <button type="button" className={`resource-subcategories__btn${!subcategory ? " resource-subcategories__btn--active" : ""}`}
            aria-pressed={!subcategory} onClick={() => setSubcategory("")}>全部</button>
          {subcategories.map((item) => (
            <button key={item} type="button"
              className={`resource-subcategories__btn${subcategory === item ? " resource-subcategories__btn--active" : ""}`}
              aria-pressed={subcategory === item} onClick={() => setSubcategory(item)}>
              {item} <span>{available.filter((e) => e.browseCategory === cat && (e.subcategory || "基础") === item).length}</span>
            </button>
          ))}
        </div>
      ) : null}

      {/* 筛选开关 */}
      <div className="resource-filters" role="group" aria-label="效果筛选">
        <button
          type="button"
          className={`resource-filters__toggle ${favOnly ? "resource-filters__toggle--on" : ""}`}
          aria-pressed={favOnly}
          onClick={() => {
            const next = !favOnly;
            setFavOnly(next);
            if (next) setRecOnly(false);
          }}
        >
          <Star size={12} />
          只看收藏
        </button>
        <button
          type="button"
          className={`resource-filters__toggle ${recOnly ? "resource-filters__toggle--on" : ""}`}
          aria-pressed={recOnly}
          onClick={() => {
            const next = !recOnly;
            setRecOnly(next);
            if (next) setFavOnly(false);
          }}
        >
          <Layers size={12} />
          最近使用
        </button>
      </div>

      {preview ? (
        <div className="resource-preview" role="status" aria-live="polite">
          <div className="resource-preview__head">
            <strong>试用预览 · {preview.name}{preview.sample ? "（工程示例片段）" : ""}</strong>
            <button type="button" className="resource-card__fav"
              aria-label={preview.loading ? "取消效果预览" : "关闭效果预览"}
              onClick={() => {
                previewRequest.current?.abort();
                previewRequest.current = null;
                if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
                previewUrl.current = null;
                setPreview(null);
              }}><X size={14} /></button>
          </div>
          {preview.loading ? <span><Loader2 size={13} className="cv-spin" /> 正在渲染选中片段…</span> : null}
          {preview.url ? <img src={preview.url} alt={`${preview.name}应用在选中片段上的效果`} /> : null}
          {preview.error ? <div className="resource-preview__failure" role="alert">
            <p className="resource-preview__error">{preview.error}</p>
            <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
              onClick={() => {
                const effect = resources.find((item) => item.effectId === preview.effectId);
                if (effect) void showPreview(effect, preview.clipId);
              }}>重试预览</button>
          </div> : null}
        </div>
      ) : null}

      {/* 列表 */}
      {loading && (resources.length === 0 || resourcesProject.current !== pid) ? (
        <div className="cv-loading" role="status">
          <Loader2 size={13} className="cv-spin" /> 加载中…
        </div>
      ) : loadError ? (
        <div className="resource-preview__failure" role="alert">
          <p>效果清单加载失败：{loadError}</p>
          <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
            onClick={refresh}>重试资源库</button>
        </div>
      ) : resources.length === 0 ? (
        <p className="cv-empty">
          {pid ? "没有匹配的效果。试试调整搜索词或分类。" : "请先创建或打开一个工程。"}
        </p>
      ) : filtered.length === 0 ? (
        <p className="cv-empty">当前素材类型和筛选条件下没有可用效果。</p>
      ) : (
        <div className="resource-grid">
          {filtered.map((e) => {
            const Icon = iconForCategory(e.category);
            const fav = favIds.includes(e.effectId) || e.favorite;
            const isTransition = e.category === "transition";
            const previewClipId = isTransition
              ? (transitionTarget.valid ? selectedClipId : sampleTargets.transition)
              : (selectedClipId || (e.appliesTo.includes("video") || e.appliesTo.includes("image")
                ? sampleTargets.first : null));
            const disabled = !selectedClipId || selectedClipReadOnly || (isTransition && !transitionTarget.valid);
            const applyTitle = state.editLock
              ? "Agent 正在编辑，暂不可应用效果"
              : selectedTrackLocked
                ? "所选片段所在轨道已锁定，解锁后可应用效果"
                : isTransition && !transitionTarget.valid
                  ? transitionTarget.reason
                : "请先在时间线选中一个片段";
            return (
              <div key={e.effectId} className="resource-card"
                draggable={isTransition && !state.editLock}
                onDragStart={(event) => {
                  if (!isTransition || state.editLock) return;
                  event.dataTransfer.effectAllowed = "copy";
                  event.dataTransfer.setData(TRANSITION_DND_TYPE, JSON.stringify({
                    effectId: e.effectId,
                    duration: typeof e.defaults.duration === "number" ? e.defaults.duration : 1,
                  }));
                  event.dataTransfer.setData("text/plain", e.name);
                }}
                title={isTransition ? "拖到视频片段接缝可直接应用或替换转场" : undefined}>
                {pid && previewClipId && e.browseCategory !== "audio" ? (
                  <ResourceThumbnail active={active} projectId={pid} clipId={previewClipId}
                    effectId={e.effectId} name={e.name} revision={String(state.project?.revision || "")}
                    sample={previewClipId !== selectedClipId}
                    cache={thumbnailUrls.current} />
                ) : null}
                <div className="resource-card__head">
                  <span className="resource-card__icon">
                    <Icon size={14} />
                  </span>
                  <span className="resource-card__name" title={e.effectId}>
                    {e.name}
                  </span>
                  <button
                    type="button"
                    className={`resource-card__fav ${fav ? "resource-card__fav--on" : ""}`}
                    aria-label={fav ? `取消收藏 ${e.name}` : `收藏 ${e.name}`}
                    aria-pressed={fav}
                    title={fav ? "取消收藏" : "收藏"}
                    onClick={() => void toggleFav(e)}
                  >
                    <Star size={13} fill={fav ? "currentColor" : "none"} />
                  </button>
                </div>
                <p className="resource-card__desc">{e.description || "（无描述）"}</p>
                <span className="resource-card__group">
                  {e.subcategory || "基础"} · {e.appliesTo.join(" / ")}
                  {typeof e.defaults.duration === "number" ? ` · ${e.defaults.duration}s` : ""}
                  {e.source === "builtin" ? " · 内置" : " · 外部效果"}
                  {e.license ? ` · ${e.license}` : ""}
                </span>
                {e.dependencies.length > 0 ? (
                  <span className="resource-card__tag" title={`依赖：${e.dependencies.join("、")}`}>
                    依赖资源
                  </span>
                ) : null}
                {e.recent ? <span className="resource-card__tag">最近</span> : null}
                {isTransition ? (
                  <span className="resource-card__tag" title="转场挂在选中片段上，并连接它前面的连续片段">
                    两段之间 · 可拖入接缝
                  </span>
                ) : null}
                <div className="resource-card__actions">
                <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
                  disabled={!previewClipId || e.browseCategory === "audio"}
                  onClick={() => { if (previewClipId) void showPreview(e, previewClipId); }}
                  title={e.browseCategory === "audio" ? "音频效果需试听，当前仅提供画面帧试用" : !previewClipId ? "工程里没有可供展示的画面片段" : previewClipId !== selectedClipId ? "在工程示例片段上试用，不修改工程" : "在当前片段上试用默认参数，不修改工程"}>
                  <Eye size={12} /> 预览
                </button>
                <button
                  type="button"
                  className="cv-btn cv-btn--sm cv-btn--secondary resource-card__apply"
                  disabled={disabled || applying === e.effectId}
                  onClick={() => void applyToSelected(e)}
                  title={disabled ? applyTitle : "应用到选中片段"}
                >
                  {applying === e.effectId ? (
                    <Loader2 size={12} className="cv-spin" />
                  ) : (
                    <CheckSquare2 size={12} />
                  )}
                  应用到选中片段
                </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p className="cv-hint" style={{ marginTop: 8 }}>
        效果栈可在右侧「效果条」里重排顺序与旁路，顺序即渲染合成顺序。
      </p>
    </Panel>
  );
}
