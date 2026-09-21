/** 中央时间线：标尺 + 播放头 + 轨道泳道（可拖拽，含跨轨）+ 缩放按钮 + 空态。
    持有共享拖拽系统（useTimelineDrag），拖动片段时渲染幽灵条块覆盖所有轨道。
    不做全局快捷键（Web 环境与浏览器冲突）；缩放走工具条按钮。 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ZoomIn, ZoomOut, Plus } from "lucide-react";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { insertClipAutoTrack, duplicateClip } from "../../store/clipEdit";
import { uploadFiles } from "../../lib/importMedia";
import { Ruler } from "./Ruler";
import { TrackRow } from "./TrackRow";
import { Playhead } from "./Playhead";
import { useTimelineDrag } from "./useTimelineDrag";
import { niceMajorStep, timelineLengthSecs, toPx, ZOOM_LEVELS, zoomLabel, fmtTime } from "./util";

export function Timeline() {
  const { state, dispatch } = useEditor();
  const [zoomIdx, setZoomIdx] = useState(3); // 40px/s 默认

  const tracks = state.project?.sequence.tracks || [];
  const pxPerSec = ZOOM_LEVELS[zoomIdx];

  // Ctrl/⌘+D：复制当前选中片段（补充右键菜单）。浏览器 Ctrl+D 默认书签，需 preventDefault。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || (e.key !== "d" && e.key !== "D")) return;
      const ael = document.activeElement as HTMLElement | null;
      const tag = (ael?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || ael?.isContentEditable) return;
      const st = getLatestState();
      const cur = st && st.currentId ? st : state;
      const sel = cur.selection;
      if (sel?.clipId) {
        e.preventDefault();
        void duplicateClip(dispatch, cur, { clipId: sel.clipId, trackId: sel.trackId });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dispatch, state]);

  const lengthSecs = useMemo(() => timelineLengthSecs(tracks), [tracks]);
  // 人类可读轨道名：按同类型出现顺序编号（视频轨 1 / 音频轨 1），替代内部 track.id
  const displayNames = useMemo(() => {
    const map = new Map<string, string>();
    let v = 0;
    let a = 0;
    for (const t of tracks) {
      map.set(t.id, t.kind === "audio" ? `音频轨 ${++a}` : `视频轨 ${++v}`);
    }
    return map;
  }, [tracks]);
  const widthPx = toPx(lengthSecs, pxPerSec);
  const totalSecs = lengthSecs;
  const playheadSecs = state.playhead;

  const dragApi = useTimelineDrag();
  const scrollerRef = useRef<HTMLDivElement | null>(null);

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

  const zoomOut = () => setZoomIdx((i) => Math.max(0, i - 1));
  const zoomIn = () => setZoomIdx((i) => Math.min(ZOOM_LEVELS.length - 1, i + 1));

  const ghost = dragApi.drop && dragApi.active?.mode === "move" ? dragApi.drop : null;
  const ghostKind = ghost?.kind === "audio" ? "audio" : "video";

  return (
    <section className={`timeline ${state.toolMode === "cut" ? "timeline--cut" : ""}`}>
      <header className="timeline__head">
        <h2 className="timeline__title">时间线</h2>
        <span className="timeline__meta">
          <span className="cv-mono">
            {fmtTime(playheadSecs)} / {fmtTime(totalSecs)}
          </span>
          <span style={{ marginLeft: 10 }}>
            {state.project.sequence.width}×{state.project.sequence.height} · {state.project.sequence.fps.num}/{state.project.sequence.fps.den}
          </span>
        </span>
        <span className="timeline__spacer" />
        <div className="timeline__zoom">
          <button className="timeline__zoom-btn" onClick={zoomOut} disabled={zoomIdx === 0} aria-label="缩小">
            <ZoomOut size={14} />
          </button>
          <span className="timeline__zoom-val">{zoomLabel(pxPerSec)}</span>
          <button className="timeline__zoom-btn" onClick={zoomIn} disabled={zoomIdx === ZOOM_LEVELS.length - 1} aria-label="放大">
            <ZoomIn size={14} />
          </button>
        </div>
      </header>

      <div className="timeline__body" ref={scrollerRef}>
        <div className="timeline__scroller">
          <div className="timeline__content" style={{ width: widthPx + 80 }}>
            <Ruler lengthSecs={lengthSecs} pxPerSec={pxPerSec} />
            <Playhead pxPerSec={pxPerSec} />
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
                <TrackRow
                  key={t.id}
                  track={t}
                  displayName={displayNames.get(t.id) || "轨道"}
                  pxPerSec={pxPerSec}
                  dragApi={dragApi}
                />
              ))
            )}
            {/* 素材拖到轨道区下方空白 → 自动建轨 */}
            <TimelineEmptyDrop />
            {ghost ? (
              <div
                className={`clip-block clip-block--${ghostKind} clip-block--ghost ${ghost.snapped ? "clip-block--ghost--snapped" : ""}`}
                style={{ left: ghost.ghostLeft, width: ghost.widthPx, top: ghost.ghostTop }}
                aria-hidden="true"
              />
            ) : null}
            {ghost ? (
              <span className="drag-time-tip" style={{ left: ghost.ghostLeft }}>
                {fmtTime(ghost.startSecs)}
              </span>
            ) : null}
          </div>
        </div>
      </div>

      <footer className="timeline__footer">
        <span>主刻度 {niceMajorStep(pxPerSec)}s</span>
        <span>{state.toolMode === "cut" ? "切割模式：点击片段在播放头分割" : "拖拽移动/跨轨 · 边缘裁剪 · 磁吸吸附"}</span>
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
      // OS 文件拖入：每个文件自动建一条视频轨并插入
      void uploadFiles(
        Array.from(files),
        (media) => insertClipAutoTrack(dispatch, getLatestState() || state, { sourcePath: media.path }),
        (name, message) => dispatch({ type: "STATUS_SET", severity: "warn", text: `导入 ${name} 失败：${message}` }),
      );
      return;
    }
    try {
      const { sourcePath } = JSON.parse(raw) as { sourcePath: string };
      void insertClipAutoTrack(dispatch, getLatestState() || state, { sourcePath });
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
      <Plus size={12} /> 拖素材或文件到此处，自动创建视频轨道
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