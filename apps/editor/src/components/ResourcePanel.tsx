import { tr, useLocale } from "../i18n";
import { readingOrderKey } from "../i18n/navigation";
import { subcategoryLabel } from "../i18n/resourceLabels";
/** 资源库面板（J01）：分类 tab / 搜索 / 收藏/最近筛选 / 效果卡片 / 应用到选中片段。
 *
 * 数据全部来自 GET /projects/{id}/resources（工程级：带 favorite/recent 标记），
 * 分类由 category 枚举派生（绝对不写死效果 ID 列表）。普通效果走 effect.add，动画走 effect.setAnimation +
 * 既有刷新机制；请求失败显式 showError，不静默降级。
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Search, Star, Loader2, Layers, GripVertical, X } from "lucide-react";
import { useEditor, showError } from "../store/editor";
import { getLatestState } from "../store/actions";
import { setFavorite } from "../store/effectEdit";
import { applyResourceToClip } from "../store/resourceEdit";
import { EFFECT_DND_TYPE, setEffectDrag, clipAcceptsEffect, type EffectDragResource } from "../lib/effectDrag";
import { rationalToSecs } from "../lib/rational";
import { TRANSITION_DND_TYPE } from "../lib/transitions";
import {
  categoryLabelOf,
  builtinPresetMediaUrl,
  fetchResourcePreview,
  iconForCategory,
  getEffectCatalog,
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
  family: "fx" | "transition" | "filter";
}

// Prefer bundled videos; missing examples render one frame after a deliberate hover.
function ResourceThumbnail({ effect, coverId, sample, playing, disabled, projectId, revision, previewClipId, onPreview }: {
  effect: ProjectResource;
  coverId?: string;
  sample: boolean;
  playing: boolean;
  disabled: boolean;
  projectId: string | null;
  revision: string | undefined;
  previewClipId: string | null;
  onPreview: (button: HTMLButtonElement) => void;
}) {
  useLocale();
  const [failed, setFailed] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [videoReady, setVideoReady] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [hoverFrame, setHoverFrame] = useState<string | null>(null);
  const [frameLoading, setFrameLoading] = useState(false);
  const [frameFailed, setFrameFailed] = useState(false);
  useEffect(() => setFailed(false), [coverId]);
  useEffect(() => { setVideoFailed(false); setVideoReady(false); }, [coverId, playing]);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const stopWhenHidden = () => {
      if (document.hidden) video.pause();
      else if (playing) void video.play().catch(() => setVideoFailed(true));
    };
    void video.play().catch(() => setVideoFailed(true));
    document.addEventListener("visibilitychange", stopWhenHidden);
    return () => { document.removeEventListener("visibilitychange", stopWhenHidden); video.pause(); };
  }, [coverId, playing]);
  useEffect(() => {
    setHoverFrame(null); setFrameFailed(false); setFrameLoading(false);
    if (!playing || sample || !projectId || !previewClipId) return;
    const controller = new AbortController();
    let url: string | null = null;
    // Missing examples render only after a deliberate hover, never while the
    // list loads or while the pointer quickly passes over several cards.
    const timer = setTimeout(() => {
      setFrameLoading(true);
      void fetchResourcePreview(projectId, previewClipId, effect.effectId, controller.signal).then(result => {
        if (result.kind === "frame") {
          if (controller.signal.aborted) { URL.revokeObjectURL(result.url); return; }
          url = result.url; setHoverFrame(url);
        } else if (!controller.signal.aborted) setFrameFailed(true);
      }).catch(() => { if (!controller.signal.aborted) setFrameFailed(true); })
        .finally(() => { if (!controller.signal.aborted) setFrameLoading(false); });
    }, 400);
    return () => { clearTimeout(timer); controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [playing, sample, projectId, revision, previewClipId, effect.effectId]);
  const Icon = iconForCategory(effect.category);
  return <button type="button" className="resource-card__thumbnail resource-card__thumbnail-button"
    disabled={disabled} onClick={event => onPreview(event.currentTarget)} aria-label={tr("预览{0}效果", effect.name)}
    title={sample ? tr("悬停预览，点击放大") : tr("预览默认样式的工程画面")}>
    {coverId && !failed ? <img loading="lazy" decoding="async" draggable={false}
      src={builtinPresetMediaUrl(coverId, "cover")} alt={tr("{0}效果示例", effect.name)}
      onError={() => setFailed(true)} /> : <span className="resource-card__thumbnail-placeholder">
      <Icon size={28} aria-hidden="true" /><span>{tr("点击预览查看效果")}</span>
    </span>}
    {playing && sample && coverId && !videoFailed ? <video ref={videoRef} key={coverId}
      className={`resource-card__hover-video${videoReady ? " resource-card__hover-video--ready" : ""}`}
      src={builtinPresetMediaUrl(coverId, "preview")} muted loop playsInline autoPlay preload="auto"
      aria-label={tr("{0}效果示例", effect.name)} onCanPlay={() => setVideoReady(true)}
      onError={() => setVideoFailed(true)} /> : null}
    {playing && videoFailed ? <span className="resource-card__preview-status">{tr("预览暂不可用，点击重试")}</span> : null}
    {playing && hoverFrame ? <img className="resource-card__hover-frame" src={hoverFrame} draggable={false}
      alt={tr("{0}应用在选中片段上的效果", effect.name)} /> : null}
    {playing && frameLoading ? <span className="resource-card__preview-status"><Loader2 size={12} className="cv-spin" />{tr(" 正在渲染选中片段…")}</span> : null}
    {playing && frameFailed ? <span className="resource-card__preview-status">{tr("预览暂不可用，点击重试")}</span> : null}
  </button>;
}

export function ResourcePanel({ active, family }: Props) {
  const locale = useLocale();
  const dragHelpId = useId();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const pid = state.currentId;

  const [resources, setResources] = useState<ProjectResource[]>([]);
  const resourcesProject = useRef<string | null>(null);
  const currentProject = useRef(pid);
  currentProject.current = pid;
  const resourcesRequest = useRef(0);
  const [builtinPresets, setBuiltinPresets] = useState<BuiltinPresetCatalog | null>(null);
  const [presetLoading, setPresetLoading] = useState(false);
  const [presetLoadError, setPresetLoadError] = useState<string | null>(null);
  const [selectedStyles, setSelectedStyles] = useState<Record<string, string>>({});
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
  const [previewingId, setPreviewingId] = useState<string | null>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const stopHover = useCallback(() => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    hoverTimer.current = null; setPreviewingId(null);
  }, []);
  const startHover = (effectId: string) => {
    stopHover();
    if (!reducedMotion) hoverTimer.current = setTimeout(() => setPreviewingId(effectId), 180);
  };
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => { setReducedMotion(query.matches); stopHover(); };
    query.addEventListener("change", changed);
    return () => { query.removeEventListener("change", changed); if (hoverTimer.current) clearTimeout(hoverTimer.current); };
  }, [stopHover]);
  useEffect(() => { stopHover(); }, [active, family, q, cat, subcategory, samplePreset, stopHover]);
  const [preview, setPreview] = useState<{ effectId: string; clipId: string; name: string; sample: boolean; url?: string; error?: string; loading: boolean } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const previewRequest = useRef<AbortController | null>(null);
  const previewUrl = useRef<string | null>(null);
  const categoryTabRefs = useRef(new Map<string, HTMLButtonElement>());
  const selectedClipId = state.selection?.clipId ?? null;
  const selectedPreviewTarget = useMemo(() => {
    const track = state.project?.sequence.tracks.find(t => t.clips.some(c => c.id === selectedClipId));
    const clip = track?.clips.find(c => c.id === selectedClipId);
    return track && clip ? { track, clip } : null;
  }, [state.project, selectedClipId]);

  // 没有视频示例的默认样式仅在用户明确点击时渲染一张工程预览帧。
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
  }, [state.project, locale]);

  const load = useCallback(
    async () => {
      const request = ++resourcesRequest.current;
      const isCurrent = () => request === resourcesRequest.current && pid === currentProject.current;
      if (!pid) {
        setResources([]);
        resourcesProject.current = null;
        setLoading(false);
        setFavIds([]);
        setLoadError(null);
        return;
      }
      setLoading(true);
      setLoadError(null);
      try {
        // 保留完整目录，搜索与两级分类在本地筛选；切分类不会让其它分类消失。
        const res = await listProjectResources(pid);
        if (!isCurrent()) return;
        setResources(res.effects);
        resourcesProject.current = pid;
        setFavIds(res.favorites);
      } catch (err) {
        if (!isCurrent()) return;
        resourcesProject.current = pid;
        try {
          const fallback = await getEffectCatalog();
          if (!isCurrent()) return;
          setResources(fallback.map(effect => ({ ...effect, favorite: false, recent: false })));
        } catch { if (isCurrent()) setResources([]); }
        if (!isCurrent()) return;
        setFavIds([]);
        setLoadError(err instanceof Error && err.message ? err.message : tr("请检查服务连接后重试。"));
        showError(dispatch, err);
      } finally {
        if (isCurrent()) setLoading(false);
      }
    },
    [pid, dispatch, locale],
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
        ? error.message : tr("内置预设审核状态暂不可用。"));
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

  const inDomain = (candidate: string) => family === "fx"
    ? candidate !== "transition" && candidate !== "filter" && candidate !== "sticker"
    : candidate === family;
  const available = useMemo(
    () => [...new Map(resources.map(effect => [effect.effectId, effect])).values()].filter(e => inDomain(e.browseCategory)),
    [resources, family, locale],
  );
  const categories = useMemo(() => sortCategories(available.map((e) => e.browseCategory)), [available, locale]);
  const categoryTabOrder = useMemo(() => ["", ...categories], [categories, locale]);
  const subcategories = useMemo(() => {
    if (!cat) return [];
    return [...new Set(available.filter((e) => e.browseCategory === cat).map((e) => e.subcategory || "基础"))].sort();
  }, [available, cat, locale]);

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
    const key = readingOrderKey(event.key, locale);
    let nextIndex: number | null = null;
    if (key === "ArrowRight" || key === "ArrowDown") nextIndex = (index + 1) % categoryTabOrder.length;
    else if (key === "ArrowLeft" || key === "ArrowUp") {
      nextIndex = (index - 1 + categoryTabOrder.length) % categoryTabOrder.length;
    } else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = categoryTabOrder.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = categoryTabOrder[nextIndex];
    selectCategory(next);
    requestAnimationFrame(() => categoryTabRefs.current.get(next)?.focus());
  };

  const stylesByEffect = useMemo(() => {
    const groups = new Map<string, BuiltinPresetSummary[]>();
    for (const preset of builtinPresets?.presets || []) {
      if (preset.status === "retired") continue;
      const styles = groups.get(preset.effectId) || [];
      styles.push(preset); groups.set(preset.effectId, styles);
    }
    return groups;
  }, [builtinPresets]);
  const styleReady = (style: BuiltinPresetSummary) => style.downloadState === "bundled" && style.mediaAvailable;
  const selectedStyle = (effectId: string) => {
    const styles = stylesByEffect.get(effectId) || [];
    const saved = selectedStyles[effectId];
    if (saved !== undefined) return styles.find(style => style.presetId === saved);
    const needle = q.trim().toLocaleLowerCase();
    return styles.find(style => styleReady(style) && needle && style.name.toLocaleLowerCase().includes(needle))
      || styles.find(style => styleReady(style));
  };
  const filtered = useMemo(() => available.filter(effect => {
    if ((favOnly && !effect.favorite) || (recOnly && !effect.recent)) return false;
    if (cat && effect.browseCategory !== cat) return false;
    if (subcategory && (effect.subcategory || "基础") !== subcategory) return false;
    const needle = q.trim().toLocaleLowerCase();
    return !needle || [effect.name, effect.description, effect.effectId, categoryLabelOf(effect.browseCategory),
      effect.subcategory, ...effect.keywords, ...(stylesByEffect.get(effect.effectId) || []).flatMap(style => [style.name, style.presetId])]
      .some(part => part.toLocaleLowerCase().includes(needle));
  }), [available, favOnly, recOnly, cat, subcategory, q, locale, stylesByEffect]);

  const openSample = (preset: BuiltinPresetSummary, button: HTMLButtonElement) => {
    sampleOpener.current = button;
    setSampleVideoError(false); setSampleVideoAttempt(0); setSamplePreset(preset);
  };

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
          error: result.kind === "empty" ? tr("所选片段在试用时间没有画面") : result.message,
          loading: false });
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setPreview({ effectId: effect.effectId, clipId, name: effect.name, sample,
        error: error instanceof Error ? error.message : tr("预览发生意外错误"),
        loading: false });
    }
  };

  const dragResource = (effect: ProjectResource, style?: BuiltinPresetSummary): EffectDragResource => ({
    effectId: effect.effectId, category: effect.category, appliesTo: style?.appliesTo || effect.appliesTo,
    duration: typeof (style?.params || effect.defaults).duration === "number" ? Number((style?.params || effect.defaults).duration) : 1,
    ...(style ? { presetId: style.presetId } : {}),
  });
  const applyWithKeyboard = async (effect: ProjectResource, style?: BuiltinPresetSummary) => {
    if (applying || (style && !styleReady(style))) return;
    setApplying(effect.effectId);
    try { await applyResourceToClip(dispatch, state, dragResource(effect, style), effect, selectedClipId || ""); }
    finally { setApplying(null); }
  };

  const toggleFav = async (effect: ProjectResource) => {
    const st = getLatestState() || state;
    await setFavorite(dispatch, st, {
      effectId: effect.effectId,
      favorite: !effect.favorite,
    });
    refresh();
  };

  return (
    <div className="resource-browser" data-resource-family={family}>
      <p className="cv-hint resource-library-help">{tr("悬停预览；选择样式后拖到片段上，转场拖到接缝。应用后可在右侧调整。")}</p>
      {state.editLock ? (
        <p className="cv-hint" role="status">
          {tr("Agent 正在编辑，效果暂不可应用。")}
        </p>
      ) : null}
      <div className="media-filter">
        <label className="media-filter__search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={tr("搜索预设、效果或分类…")}
            aria-label={tr("搜索预设和效果")}
          />
        </label>
      </div>
      {presetLoading && !builtinPresets ? <p className="cv-loading" role="status">{tr(" 加载中…")}</p> : null}
      {presetLoadError ? <div className="resource-preview__failure" role="alert">
        <p>{tr("内置预设审核状态读取失败：")}{presetLoadError}</p><button type="button" className="cv-button cv-button--small" onClick={() => void loadBuiltinPresets()}>{tr("重试内置预设")}</button>
      </div> : null}
      {loadError ? <div className="resource-preview__failure" role="alert">
        <p>{tr("效果清单加载失败：")}{loadError}</p>
        <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary" onClick={refresh}>{tr("重试资源库")}</button>
      </div> : null}
      {samplePreset ? (
        <div className="resource-sample-overlay"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setSamplePreset(null);
              requestAnimationFrame(() => sampleOpener.current?.focus());
            }
          }}>
          <section className="resource-sample-dialog" role="dialog" aria-modal="true"
            aria-label={tr("{0}动态样片", samplePreset.name)}
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
                <span>{categoryLabelOf(samplePreset.family as EffectCategory)} · {subcategoryLabel(samplePreset.subcategory)} · {samplePreset.license} · v{samplePreset.version}</span>
                <small className="cv-hint" title={samplePreset.source || tr("来源未记录")}>{tr("来源：")}{samplePreset.source || tr("未记录")}
                </small>
              </div>
              <button ref={sampleCloseButton} type="button"
                aria-label={tr("关闭动态样片")}
                onClick={() => {
                  setSamplePreset(null);
                  requestAnimationFrame(() => sampleOpener.current?.focus());
                }}><X size={18} /></button>
            </div>
            <video key={`${samplePreset.presetId}-${sampleVideoAttempt}`}
              controls autoPlay muted loop playsInline tabIndex={0} preload="metadata"
              poster={builtinPresetMediaUrl(samplePreset.presetId, "cover")}
              src={builtinPresetMediaUrl(samplePreset.presetId, "preview")}
              aria-label={tr("{0}动态样片视频", samplePreset.name)}
              onError={() => setSampleVideoError(true)}
              onLoadedData={() => setSampleVideoError(false)} />
            {sampleVideoError ? (
              <div className="resource-preview__failure" role="alert">
                <span>{tr("动态样片加载失败，请检查本地预设资源后重试。")}</span>
                <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
                  aria-label={tr("重试动态样片")}
                  onClick={() => {
                    setSampleVideoError(false);
                    setSampleVideoAttempt((attempt) => attempt + 1);
                  }}>{tr("重试")}</button>
              </div>
            ) : null}
            <p>{tr("示例样片展示效果变化；应用后可在时间线继续调整。")}</p>
          </section>
        </div>
      ) : null}
      <section className="resource-effect-library" aria-label={tr("效果库")}>
      <p id={dragHelpId} className="sr-only">{tr("拖到目标片段；键盘可选中效果卡片后按 Enter 应用到选中片段")}</p>
      <p className="resource-library-count" role="status">{filtered.length}{tr(" 个效果")}</p>
      {/* 分类 tab：由 category 枚举派生 */}
      {categories.length > 1 && <div className="resource-tabs" role="tablist" aria-label={tr("效果分类")}>
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
        >{tr("全部")}</button>
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
      </div>}

      {cat && subcategories.length > 1 ? (
        <div className="resource-subcategories" role="group" aria-label={tr("{0}细分类", categoryLabelOf(cat))}>
          <button type="button" className={`resource-subcategories__btn${!subcategory ? " resource-subcategories__btn--active" : ""}`}
            aria-pressed={!subcategory} onClick={() => setSubcategory("")}>{tr("全部")}</button>
          {subcategories.map((item) => (
            <button key={item} type="button"
              className={`resource-subcategories__btn${subcategory === item ? " resource-subcategories__btn--active" : ""}`}
              aria-pressed={subcategory === item} onClick={() => setSubcategory(item)}>
              {subcategoryLabel(item)} <span>{available.filter((e) => e.browseCategory === cat && (e.subcategory || "基础") === item).length}</span>
            </button>
          ))}
        </div>
      ) : null}

      {/* 筛选开关 */}
      <div className="resource-filters" role="group" aria-label={tr("效果筛选")}>
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
          <Star size={12} />{tr("只看收藏")}</button>
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
          <Layers size={12} />{tr("最近使用")}</button>
      </div>

      {preview ? (
        <div className="resource-preview" role="status" aria-live="polite">
          <div className="resource-preview__head">
            <strong>{tr("试用预览 · ")}{preview.name}{preview.sample ? tr("（工程示例片段）") : ""}</strong>
            <button type="button" className="resource-card__fav"
              aria-label={preview.loading ? tr("取消效果预览") : tr("关闭效果预览")}
              onClick={() => {
                previewRequest.current?.abort();
                previewRequest.current = null;
                if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
                previewUrl.current = null;
                setPreview(null);
              }}><X size={14} /></button>
          </div>
          {preview.loading ? <span><Loader2 size={13} className="cv-spin" />{tr(" 正在渲染选中片段…")}</span> : null}
          {preview.url ? <img src={preview.url} alt={tr("{0}应用在选中片段上的效果", preview.name)} /> : null}
          {preview.error ? <div className="resource-preview__failure" role="alert">
            <p className="resource-preview__error">{preview.error}</p>
            <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
              onClick={() => {
                const effect = resources.find((item) => item.effectId === preview.effectId);
                if (effect) void showPreview(effect, preview.clipId);
              }}>{tr("重试预览")}</button>
          </div> : null}
        </div>
      ) : null}

      {/* 列表 */}
      {(pid && resourcesProject.current !== pid) || (loading && resources.length === 0) ? (
        <div className="cv-loading" role="status">
          <Loader2 size={13} className="cv-spin" />{tr(" 加载中…")}</div>
      ) : loadError && resources.length === 0 ? null : resources.length === 0 ? (
        <p className="cv-empty">
          {pid ? tr("没有匹配的效果。试试调整搜索词或分类。") : tr("请先创建或打开一个工程。")}
        </p>
      ) : filtered.length === 0 ? (
        <p className="cv-empty">{tr("当前素材类型和筛选条件下没有可用效果。")}</p>
      ) : (
        <div className="resource-grid resource-effect-grid">
          {filtered.map((e) => {
            const Icon = iconForCategory(e.category);
            const fav = favIds.includes(e.effectId) || e.favorite;
            const styles = stylesByEffect.get(e.effectId) || [];
            const style = selectedStyle(e.effectId);
            const sample = !!style && styleReady(style);
            const isTransition = e.category === "transition";
            const compatibleSelection = selectedPreviewTarget && clipAcceptsEffect(e, selectedPreviewTarget.track, selectedPreviewTarget.clip)
              ? selectedClipId : null;
            const previewClipId = isTransition
              ? (compatibleSelection && selectedPreviewTarget?.track.kind === "video" && compatibleSelection !== sampleTargets.first ? compatibleSelection : sampleTargets.transition)
              : (compatibleSelection || (e.appliesTo.includes("video") || e.appliesTo.includes("image")
                ? sampleTargets.first : null));
            const dragDisabled = !!state.editLock || (!!style && !styleReady(style));
            return (
              <div key={e.effectId} className="resource-card resource-effect-card" data-effect-id={e.effectId}
                role="group" tabIndex={0} aria-label={tr("{0}效果卡片", e.name)}
                aria-describedby={dragHelpId}
                draggable={!dragDisabled}
                onMouseEnter={() => startHover(e.effectId)} onMouseLeave={stopHover}
                onFocus={event => { if (!event.currentTarget.contains(event.relatedTarget as Node) && event.target.matches(":focus-visible")) startHover(e.effectId); }}
                onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) stopHover(); }}
                onKeyDown={event => {
                  if (event.target === event.currentTarget && !event.repeat && event.key === "Enter") {
                    event.preventDefault(); void applyWithKeyboard(e, style);
                  }
                }}
                onDragStart={(event) => {
                  if (dragDisabled || (event.target as HTMLElement).closest("select,option,input")) { event.preventDefault(); return; }
                  stopHover();
                  const resource = dragResource(e, style);
                  setEffectDrag(resource);
                  event.dataTransfer.effectAllowed = "copy";
                  event.dataTransfer.setData(isTransition ? TRANSITION_DND_TYPE : EFFECT_DND_TYPE, JSON.stringify(resource));
                  event.dataTransfer.setData("text/plain", e.name);
                }}
                onDragEnd={() => setEffectDrag(null)}
                title={tr("拖到目标片段；键盘可选中效果卡片后按 Enter 应用到选中片段")}>
                {e.browseCategory !== "audio" ? (
                  <ResourceThumbnail effect={e} sample={sample} playing={active && previewingId === e.effectId && !samplePreset && !reducedMotion}
                    coverId={style?.presetId || styles.find(styleReady)?.presetId}
                    projectId={pid} revision={state.project?.revision} previewClipId={previewClipId}
                    disabled={!sample && !previewClipId} onPreview={button => {
                      stopHover();
                      if (style && sample) openSample(style, button);
                      else if (previewClipId) void showPreview(e, previewClipId);
                    }} />
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
                    aria-label={fav ? tr("取消收藏 {0}", e.name) : tr("收藏 {0}", e.name)}
                    aria-pressed={fav}
                    title={fav ? tr("取消收藏") : tr("收藏")}
                    onClick={() => void toggleFav(e)}
                  >
                    <Star size={13} fill={fav ? "currentColor" : "none"} />
                  </button>
                </div>
                <p className="resource-card__desc">{e.description || tr("（无描述）")}</p>
                <label className="resource-card__style">{tr("预设样式")}
                  <select aria-label={tr("{0}的预设样式", e.name)} value={style?.presetId || ""}
                    onChange={event => {
                      if (event.currentTarget.closest(".resource-card")?.matches(":hover")) startHover(e.effectId);
                      else stopHover();
                      setSelectedStyles(current => ({ ...current, [e.effectId]: event.target.value }));
                      previewRequest.current?.abort();
                      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
                      previewUrl.current = null; setPreview(null);
                    }}>
                    <option value="">{tr("默认样式")}</option>
                    {styles.map(item => <option key={item.presetId} value={item.presetId}
                      disabled={!styleReady(item)}>
                      {item.effects.length > 1 ? tr("{0}（组合样式）", item.name) : item.name}
                      {!styleReady(item) ? tr("（暂不可用）") : ""}
                    </option>)}
                  </select>
                </label>
                <span className="resource-card__drag-hint"><GripVertical size={12} aria-hidden="true" />
                  {isTransition ? tr("拖到接缝") : tr("拖到片段")}
                </span>
              </div>
            );
          })}
        </div>
      )}


      </section>
    </div>
  );
}
