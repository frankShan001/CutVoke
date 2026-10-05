/** 中央时间线：标尺 + 播放头 + 轨道泳道（可拖拽，含跨轨）+ 缩放按钮 + 空态。
    持有共享拖拽系统（useTimelineDrag），拖动片段时渲染幽灵条块覆盖所有轨道。 */

import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowLeftRight, CircleHelp, LocateFixed, Maximize2, ZoomIn, ZoomOut, Plus } from "lucide-react";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { insertClipAutoTrack, duplicateClip } from "../../store/clipEdit";
import { trackKindForMedia, uploadFiles } from "../../lib/importMedia";
import { inferKind, type SessionAsset } from "../../lib/assetStore";
import { Ruler } from "./Ruler";
import { TrackRow } from "./TrackRow";
import { EffectTrackRow } from "./EffectTrackRow";
import { CaptionTrackRow } from "./CaptionTrackRow";
import { Playhead } from "./Playhead";
import { useTimelineDrag } from "./useTimelineDrag";
import { niceMajorStep, timelineLengthSecs, trackEndSecs, toPx, ZOOM_LEVELS, zoomLabel, fmtTime, fmtTimePrecise } from "./util";
import { playbackEndSecs } from "../playerUtils";
import { getEffectCatalog, type EffectSpec } from "../../lib/effects";
import { useTimelineViewport } from "./useTimelineViewport";

function TimelineClock({ end, revealTime }: { end: number; revealTime: (time: number, force?: boolean) => void }) {
  const { state } = useEditor();
  const previous = useRef(state.playhead);
  useLayoutEffect(() => {
    if (Math.abs(previous.current - state.playhead) < 0.0001) return;
    previous.current = state.playhead;
    revealTime(state.playhead);
  }, [state.playhead, revealTime]);
  return <span className="cv-mono">{fmtTimePrecise(state.playhead)} / {fmtTime(end)}</span>;
}

export function Timeline() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [zoomIdx, setZoomIdx] = useState(3); // 40px/s 默认
  const [effectSpecs, setEffectSpecs] = useState<EffectSpec[]>([]);

  const tracks = state.project?.sequence.tracks || [];
  const captions = state.project?.sequence.captions || [];
  const pxPerSec = ZOOM_LEVELS[zoomIdx];

  // Ctrl/⌘+D：复制当前选中片段（补充右键菜单）。浏览器 Ctrl+D 默认书签，需 preventDefault。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || (e.key !== "d" && e.key !== "D")) return;
      if (e.defaultPrevented || e.isComposing || state.view !== "editor" || state.editLock || state.exportOpen || state.templateOpen || state.agentPanelOpen) return;
      const ael = document.activeElement as HTMLElement | null;
      const tag = (ael?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || ael?.isContentEditable || ael?.closest("[role='dialog'], [role='alertdialog']")) return;
      const st = getLatestState();
      const cur = st && st.currentId ? st : state;
      const sel = cur.selection;
      if (sel?.clipId) {
        e.preventDefault();
        const selectedTrack = cur.project?.sequence.tracks.find((track) => track.id === sel.trackId);
        if (selectedTrack?.locked || cur.editLock) {
          dispatch({
            type: "STATUS_SET",
            severity: "warn",
            text: selectedTrack?.locked
              ? "所选片段所在轨道已锁定，解锁后才能复制"
              : "Agent 正在编辑，暂不能复制片段",
          });
          return;
        }
        void duplicateClip(dispatch, cur, { clipId: sel.clipId, trackId: sel.trackId });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dispatch, state]);

  const lengthSecs = useMemo(() => timelineLengthSecs(tracks), [tracks]);
  const contentEndSecs = useMemo(() => Math.max(0, ...tracks.map(trackEndSecs)), [tracks]);
  const playableEndSecs = useMemo(() => playbackEndSecs(tracks), [tracks]);
  // 人类可读轨道名：按同类型出现顺序编号（视频轨 1 / 音频轨 1），替代内部 track.id
  const displayNames = useMemo(() => {
    const map = new Map<string, string>();
    let v = 0;
    let a = 0;
    let s = 0;
    let text = 0;
    for (const t of tracks) {
      map.set(t.id, t.role === "sticker" ? `贴纸轨 ${++s}`
        : t.kind === "audio" ? `音频轨 ${++a}`
          : t.kind === "text" ? `文字轨 ${++text}` : `视频轨 ${++v}`);
    }
    return map;
  }, [tracks]);
  const widthPx = toPx(lengthSecs, pxPerSec);
  const currentPlayhead = () => (getLatestState() || state).playhead;

  const dragApi = useTimelineDrag();
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const fittedProjectRef = useRef<string | null>(null);
  const viewport = useTimelineViewport(scrollerRef, pxPerSec);
  const zoomAnchorRef = useRef<{ time: number; viewportX: number } | null>(null);

  const getAxis = useCallback(() => {
    const scroller = scrollerRef.current?.querySelector(".timeline__scroller") as HTMLElement | null;
    const ruler = scrollerRef.current?.querySelector(".timeline-ruler") as HTMLElement | null;
    if (!scroller || !ruler) return null;
    const axisStart = ruler.getBoundingClientRect().left - scroller.getBoundingClientRect().left + scroller.scrollLeft;
    return { scroller, axisStart };
  }, []);

  const revealTime = useCallback((time: number, force = false) => {
    const axis = getAxis();
    if (!axis) return;
    const { scroller, axisStart } = axis;
    const x = axisStart + Math.max(0, time) * pxPerSec;
    const margin = Math.min(72, scroller.clientWidth * 0.15);
    if (force || x < scroller.scrollLeft + margin || x > scroller.scrollLeft + scroller.clientWidth - margin) {
      scroller.scrollLeft = Math.max(0, x - scroller.clientWidth * 0.4);
    }
  }, [getAxis, pxPerSec]);

  // A zoom click keeps the current frame under the same point in the viewport.
  // This makes close frame edits possible even deep in a long sequence.
  useLayoutEffect(() => {
    const anchor = zoomAnchorRef.current;
    if (!anchor) return;
    zoomAnchorRef.current = null;
    const axis = getAxis();
    if (!axis) return;
    axis.scroller.scrollLeft = Math.max(0, axis.axisStart + anchor.time * pxPerSec - anchor.viewportX);
  }, [zoomIdx, getAxis, pxPerSec]);

  useEffect(() => {
    let cancelled = false;
    getEffectCatalog()
      .then((items) => {
        if (!cancelled) setEffectSpecs(items);
      })
      .catch(() => {
        // 时间轴仍可用 effectId 回退显示；详细错误由资源/属性面板负责呈现。
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const zoomToFit = useCallback(() => {
    const scroller = scrollerRef.current?.querySelector(".timeline__scroller") as HTMLElement | null;
    const ruler = scrollerRef.current?.querySelector(".timeline-ruler") as HTMLElement | null;
    if (!scroller || !ruler) return false;
    const axisStart = ruler.getBoundingClientRect().left - scroller.getBoundingClientRect().left + scroller.scrollLeft;
    const axisWidth = Math.max(0, scroller.clientWidth - axisStart);
    const fitDurationSecs = contentEndSecs > 0 ? contentEndSecs : lengthSecs;
    const fitIndex = ZOOM_LEVELS.reduce(
      (best, level, index) => (level * fitDurationSecs <= axisWidth ? index : best),
      0,
    );
    setZoomIdx(fitIndex);
    scroller.scrollLeft = 0;
    return true;
  }, [contentEndSecs, lengthSecs]);

  // On first entry, show as much of the project as the current viewport allows.
  // Subsequent content updates do not override a zoom level chosen by the user.
  useEffect(() => {
    const projectId = state.currentId;
    if (!projectId || state.project?.projectId !== projectId || fittedProjectRef.current === projectId) return;
    const frameId = window.requestAnimationFrame(() => {
      if (zoomToFit()) fittedProjectRef.current = projectId;
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [state.currentId, state.project?.projectId, zoomToFit]);

  if (!state.project) {
    return (
      <section className="timeline">
        <header className="timeline__head">
          <h2 className="timeline__title">时间线</h2>
        </header>
        <div style={{ padding: 24 }}>
          <p className="cv-empty">尚未选择工程。请在左侧创建或选择一个工程。</p>
        </div>
      </section>
    );
  }

  const zoomAroundPlayhead = (direction: -1 | 1) => {
    const playheadSecs = currentPlayhead();
    const next = Math.max(0, Math.min(ZOOM_LEVELS.length - 1, zoomIdx + direction));
    if (next === zoomIdx) return;
    const axis = getAxis();
    if (axis) {
      const x = axis.axisStart + playheadSecs * pxPerSec - axis.scroller.scrollLeft;
      zoomAnchorRef.current = {
        time: playheadSecs,
        viewportX: Math.max(48, Math.min(axis.scroller.clientWidth - 48, x)),
      };
    }
    setZoomIdx(next);
  };

  const ghost = dragApi.drop && dragApi.active?.mode === "move" ? dragApi.drop : null;
  const ghostKind = ghost?.kind === "audio" ? "audio" : "video";
  const ghostFollowers = dragApi.active?.clipId
    ? tracks.flatMap((track) => track.clips).filter((clip) => clip.attachedToClipId === dragApi.active?.clipId).length
    : 0;

  return (
    <section className={`timeline ${state.toolMode === "cut" ? "timeline--cut" : ""}`}>
      <header className="timeline__head">
        <h2 className="timeline__title">时间线</h2>
        <span
          className="timeline__meta"
          title="时间码显示播放头与最后一个片段的结束点；标尺会额外预留空白，便于继续添加片段。"
        >
          <span className="timeline__duration-label">播放头 / 片段末尾</span>
          <TimelineClock end={contentEndSecs} revealTime={revealTime} />
          <span className="timeline__format">
            {state.project.sequence.width}×{state.project.sequence.height} · {state.project.sequence.fps.num}/{state.project.sequence.fps.den}
          </span>
        </span>
        <span className="timeline__spacer" />
        <button
          className={`tool-btn ${state.mainTrackAutoFitEnabled ? "tool-btn--on" : ""}`}
          onClick={() => dispatch({ type: "MAIN_TRACK_AUTO_FIT_TOGGLE" })}
          aria-label="主轨自动贴合开关"
          aria-pressed={state.mainTrackAutoFitEnabled}
          title={state.mainTrackAutoFitEnabled
            ? "主轨自动贴合：开启。拖入主视频轨会按顺序插入并压紧片段；关闭后可保留空隙。"
            : "主轨自动贴合：关闭。拖动可在主视频轨保留空隙。"}
        >
          <ArrowLeftRight size={14} />
        </button>
        <button className="timeline__zoom-btn" onClick={() => revealTime(currentPlayhead(), true)} aria-label="定位播放头" title="把播放头定位到时间线视野中">
          <LocateFixed size={14} />
        </button>
        <div className="timeline__zoom">
          <button className="timeline__zoom-btn" onClick={zoomToFit} aria-label="适应工程" title="缩放至完整显示当前工程">
            <Maximize2 size={14} />
          </button>
          <button className="timeline__zoom-btn" onClick={() => zoomAroundPlayhead(-1)} disabled={zoomIdx === 0} aria-label="缩小" title="以播放头为中心缩小">
            <ZoomOut size={14} />
          </button>
          <span className="timeline__zoom-val">{zoomLabel(pxPerSec)}</span>
          <button className="timeline__zoom-btn" onClick={() => zoomAroundPlayhead(1)} disabled={zoomIdx === ZOOM_LEVELS.length - 1} aria-label="放大" title="以播放头为中心放大">
            <ZoomIn size={14} />
          </button>
        </div>
        <details className="timeline__shortcuts">
          <summary className="timeline__zoom-btn" aria-label="查看快捷键" title="查看快捷键"><CircleHelp size={14} /></summary>
          <div className="timeline__shortcuts-card">
            <strong>常用快捷键</strong>
            <dl>
              <dt>播放 / 暂停</dt><dd>空格</dd>
              <dt>选择 / 切割工具</dt><dd>V / C</dd>
              <dt>删除所选片段或效果</dt><dd>Delete / Backspace</dd>
              <dt>复制所选片段</dt><dd>Ctrl / ⌘ + D</dd>
              <dt>撤销 / 重做</dt><dd>Ctrl / ⌘ + Z · Ctrl / ⌘ + Shift + Z</dd>
              <dt>逐帧定位</dt><dd>先点标尺，再按 ← / →</dd>
              <dt>片段前后重排</dt><dd>先聚焦片段，再按 Alt + ← / →</dd>
              <dt>临时独立移动</dt><dd>按住 Alt 拖动视频，关联片段保持原位</dd>
            </dl>
            <small>输入文字或打开弹窗时，编辑器快捷键暂停响应。</small>
          </div>
        </details>
      </header>

      <div className="timeline__body" ref={scrollerRef}>
        <div className="timeline__scroller">
          <div
            className="timeline__content"
            style={{
              width: `calc(${widthPx + 80}px + var(--track-label-w) + var(--space-2) + var(--timeline-content-inset-x) + var(--timeline-content-inset-x))`,
            }}
          >
            <Ruler lengthSecs={lengthSecs} maxSeekSecs={playableEndSecs} pxPerSec={pxPerSec} />
            <Playhead pxPerSec={pxPerSec} maxSecs={playableEndSecs} />
            {tracks.length === 0 ? (
              <div className="timeline__emptystate">
                <div className="timeline__emptystate-text">
                  这是一个空工程。导入素材并拖到时间线，即可开始剪辑。
                </div>
                <button
                  className="tool-btn tool-btn--export timeline__emptystate-btn"
                  onClick={() => document.dispatchEvent(new CustomEvent("cutvoke:open-import"))}
                  aria-label="导入素材"
                >
                  <ImportFileIcon /> 导入素材
                </button>
                <div className="timeline__emptystate-hint">或拖拽文件到下方空白区自动创建轨道</div>
              </div>
            ) : (
              tracks.map((t) => (
                <Fragment key={t.id}>
                  <TrackRow
                    track={t}
                    displayName={displayNames.get(t.id) || "轨道"}
                    pxPerSec={pxPerSec}
                    dragApi={dragApi}
                    effectSpecs={effectSpecs}
                    viewport={viewport}
                  />
                  {t.kind === "video" ? (
                    <EffectTrackRow
                      track={t}
                      displayName={`效果轨 ${videoTrackOrdinal(tracks, t.id)}`}
                      pxPerSec={pxPerSec}
                      effectSpecs={effectSpecs}
                      viewport={viewport}
                    />
                  ) : null}
                </Fragment>
              ))
            )}
            {captions.length > 0 ? (
              <CaptionTrackRow
                captions={captions}
                selectedCaptionId={state.selectedCaptionId}
                pxPerSec={pxPerSec}
              />
            ) : null}
            {/* 素材拖到轨道区下方空白 → 自动建轨 */}
            <TimelineEmptyDrop />
            {ghost ? (
              <div
                className={`clip-block clip-block--${ghostKind} clip-block--ghost ${ghost.snapped ? "clip-block--ghost--snapped" : ""} ${ghost.reorderMode ? "clip-block--ghost--reorder" : ""} ${ghost.invalidReason ? "clip-block--ghost--invalid" : ""}`}
                style={{ left: ghost.ghostLeft, width: ghost.widthPx, top: ghost.ghostTop }}
                title={ghost.invalidReason || `${ghost.mainTrackAutoFit ? "主轨自动贴合插入" : ghost.reorderMode ? "插入片段间隙" : "放置位置"}：${fmtTime(ghost.startSecs)}`}
                aria-hidden="true"
              />
            ) : null}
            {ghost?.reorderMode && ghost.insertLeft !== undefined ? (
              <div
                className="timeline-insert-caret"
                style={{ left: ghost.insertLeft, top: ghost.ghostTop - 6 }}
                aria-hidden="true"
              />
            ) : null}
            {ghost ? (
              <span
                className={`drag-time-tip ${ghost.reorderMode ? "drag-time-tip--reorder" : ""} ${ghost.invalidReason ? "drag-time-tip--invalid" : ""}`}
                style={{
                  left: ghost.reorderMode && ghost.insertLeft !== undefined ? ghost.insertLeft : ghost.ghostLeft,
                  top: Math.max(0, ghost.ghostTop - 5),
                }}
              >
                {ghost.invalidReason || `${ghost.mainTrackAutoFit ? "主轨贴合 · 插入到这里 · " : ghost.reorderMode ? "插入到这里 · " : ""}${fmtTime(ghost.startSecs)}${ghostFollowers ? ` · 跟随 ${ghostFollowers} 个片段` : ""}`}
              </span>
            ) : null}
          </div>
        </div>
      </div>

      <footer className="timeline__footer">
        <span>主刻度 {niceMajorStep(pxPerSec)}s</span>
        <span title="单击选中片段；拖动片段可移动或跨轨，拖动边缘可裁剪；Delete/Backspace 删除所选片段或效果。磁吸可在顶部工具栏切换。">
          {state.toolMode === "cut" ? "切割模式：点击片段在播放头分割" : "单击选中 · 拖动移动/跨轨 · 边缘裁剪 · Delete 删除"}
        </span>
        <span title="聚焦片段后按 Alt+← 或 Alt+→，可把整段移到相邻片段之前或之后。">Alt+←/→ 调整片段顺序</span>
        <span style={{ flex: 1 }} />
        <span>{tracks.length} 轨道 · {clipCount(tracks)} 片段</span>
      </footer>
    </section>
  );
}

/** 轨道列表下方空白：拖「库内素材」或「系统文件」到此，自动创建视频轨并插入。 */
function TimelineEmptyDrop() {
  const { state, dispatch } = useEditor();
  const [over, setOver] = useState(false);

  const handleDragOver = (e: React.DragEvent) => {
    const types = e.dataTransfer.types;
    if (!types.includes("text/cutvoke-media") && !types.includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    if (!over) setOver(true);
  };
  const handleDrop = (e: React.DragEvent) => {
    setOver(false);
    const files = e.dataTransfer.files;
    const hasFiles = !!files && files.length > 0;
    const raw = hasFiles ? "" : e.dataTransfer.getData("text/cutvoke-media");
    if (!hasFiles && !raw) return;
    e.preventDefault();
    e.stopPropagation();
    if (hasFiles) {
      // OS 文件拖入：按探测到的素材类型自动创建视频轨或音频轨。
      void uploadFiles(
        Array.from(files),
        (media) => insertClipAutoTrack(dispatch, getLatestState() || state, {
          sourcePath: media.path,
          assetId: media.assetId,
          trackKind: trackKindForMedia(media.kind),
        }),
        (name, message) => dispatch({ type: "STATUS_SET", severity: "warn", text: `导入 ${name} 失败：${message}` }),
      );
      return;
    }
    try {
      const payload = JSON.parse(raw) as { sourcePath: string; assetId?: string;
        kind?: SessionAsset["kind"]; role?: "sticker";
        stickerAnimation?: { effectId: string; params: Record<string, unknown> };
        stickerScale?: number;
        resourceRef?: import("../../types/api").ResourceReference };
      const kind = payload.kind ?? inferKind(payload.sourcePath, false, false);
      void insertClipAutoTrack(dispatch, getLatestState() || state, {
        sourcePath: payload.sourcePath,
        assetId: payload.assetId,
        trackKind: payload.role === "sticker" ? "video" : trackKindForMedia(kind),
        trackRole: payload.role === "sticker" ? "sticker" : undefined,
        stickerAnimation: payload.role === "sticker" ? payload.stickerAnimation : undefined,
        resourceRef: payload.role === "sticker" ? payload.resourceRef : undefined,
        stickerScale: payload.role === "sticker" ? payload.stickerScale : undefined,
      });
    } catch {
      /* ignore */
    }
  };
  return (
    <div
      className={`timeline__empty-area ${over ? "timeline__empty-area--over" : ""}`}
      onDragOver={handleDragOver}
      onDragLeave={() => setOver(false)}
      onDrop={handleDrop}
    >
      <Plus size={12} /> 拖素材或文件到此处，自动创建对应类型轨道
    </div>
  );
}

function clipCount(tracks: { clips?: unknown[] }[]): number {
  return tracks.reduce((n, t) => n + (t.clips?.length || 0), 0);
}

/** 导入图标（内联 SVG，免额外依赖）。 */
function ImportFileIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

function videoTrackOrdinal(tracks: { id: string; kind: string }[], trackId: string): number {
  const index = tracks.filter((track) => track.kind === "video").findIndex((track) => track.id === trackId);
  return Math.max(1, index + 1);
}
