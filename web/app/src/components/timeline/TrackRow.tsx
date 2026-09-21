/** 轨道泳道（配合共享拖拽系统）。
    - 片段整体拖动 → Timeline 全局拖拽（同轨 / 跨轨 = clip.move）。
    - 左/右缘拖拽 = clip.trim（本地 overlay + 释放提交，只改一端）。
    空白点击 = 取消选中；右键 = 上下文菜单。 */

import { useRef, useState } from "react";
import { Film, Music, Lock, Unlock, Volume2, VolumeX, Eye, EyeOff } from "lucide-react";
import type { Clip, Track } from "../../types/api";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { trimClip, insertClipFromDrop, splitClip, updateTrack } from "../../store/clipEdit";
import { ClipContextMenu, type CtxTarget } from "../ClipContextMenu";
import { clipStartSecs, clipEndSecs, toPx, pxToSecSnapped, secsToRatString } from "./util";
import { rationalToSecs, secsToRational } from "../../lib/rational";
import { sourceBasename } from "../../lib/media";
import { uploadFiles } from "../../lib/importMedia";
import type { TimelineDragApi } from "./useTimelineDrag";

interface TrimState {
  clipId: string;
  mode: "trim-l" | "trim-r";
  originStart: number;
  originEnd: number;
}

interface TrimShape {
  left: number;
  width: number;
}

export function TrackRow({
  track,
  displayName,
  pxPerSec,
  dragApi,
}: {
  track: Track;
  /** 人类可读轨道名（如「视频轨 1」），替代内部 track.id。 */
  displayName: string;
  pxPerSec: number;
  dragApi: TimelineDragApi;
}) {
  const { state, dispatch } = useEditor();
  const isAudio = track.kind === "audio";
  // 轨道属性（后端 model 已有 locked/muted/visible；visible 缺省视为显示）
  const locked = !!track.locked;
  const muted = !!track.muted;
  const hidden = track.visible === false;
  const [trim, setTrim] = useState<TrimState | null>(null);
  const [trimShape, setTrimShape] = useState<{ clipId: string; shape: TrimShape } | null>(null);
  const [menuTarget, setMenuTarget] = useState<CtxTarget | null>(null);
  const [dropOver, setDropOver] = useState(false);
  const trimPointerId = useRef<number | null>(null);

  const laneFocused = state.selection?.trackId === track.id && !state.selection?.clipId;

  // 切换轨道属性：走 track.update 命令（缺省字段不变）
  const handleToggle = (field: "locked" | "muted" | "visible", value: boolean) => {
    const st = getLatestState() || state;
    void updateTrack(dispatch, st, { trackId: track.id, [field]: value });
  };

  // 切割模式：点击片段 → 在播放头处分割（若播放头在片段内）；否则选择
  const handleCutClick = (e: React.MouseEvent, clip: Clip) => {
    if (state.toolMode === "cut") {
      e.preventDefault();
      e.stopPropagation();
      const cs = rationalToSecs(clip.timelineStart);
      const ce = rationalToSecs(clip.timelineEnd);
      if (state.playhead > cs + 0.01 && state.playhead < ce - 0.01) {
        const st = getLatestState() || state;
        void splitClip(dispatch, st, { clipId: clip.id, time: secsToRational(state.playhead) });
      } else {
        dispatch({ type: "STATUS_SET", severity: "warn", text: "播放头需在片段内部才能分割" });
      }
    } else {
      dispatch({ type: "SELECTION_SET", selection: { trackId: track.id, clipId: clip.id } });
    }
  };

  // ---- 拖入本轨（HTML5 DnD）：库内素材 or OS 文件 ----
  const handleDragOver = (e: React.DragEvent) => {
    const types = e.dataTransfer.types;
    if (!types.includes("text/cutvoke-media") && !types.includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    if (!dropOver) setDropOver(true);
  };
  const handleDragLeave = (e: React.DragEvent) => {
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDropOver(false);
  };
  const handleDrop = (e: React.DragEvent) => {
    setDropOver(false);
    const files = e.dataTransfer.files;
    const hasFiles = !!files && files.length > 0;
    const raw = hasFiles ? "" : e.dataTransfer.getData("text/cutvoke-media");
    if (!hasFiles && !raw) return;
    e.preventDefault();
    e.stopPropagation();
    // 计算 drop 时间线位置（px→sec）
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const scroller = (e.currentTarget as HTMLElement).closest(".timeline__scroller") as HTMLElement | null;
    const scrollLeft = scroller ? scroller.scrollLeft : 0;
    const startSecs = Math.max(0, (e.clientX - rect.left + scrollLeft) / pxPerSec);
    const ctx = getLatestState() || state;
    if (hasFiles) {
      // OS 文件拖入：上传 → 依次插入本轨（按累计时长错位，避免重叠）
      let cursor = startSecs;
      void uploadFiles(
        Array.from(files),
        (media) => {
          const at = cursor;
          cursor += media.duration && media.duration > 0 ? media.duration : 2;
          return insertClipFromDrop(dispatch, getLatestState() || ctx, {
            trackId: track.id,
            sourcePath: media.path,
            timelineStartSecs: at,
          });
        },
        (name, message) => dispatch({ type: "STATUS_SET", severity: "warn", text: `导入 ${name} 失败：${message}` }),
      );
      return;
    }
    let payload: { sourcePath: string };
    try {
      payload = JSON.parse(raw);
    } catch {
      return;
    }
    void insertClipFromDrop(dispatch, ctx, {
      trackId: track.id,
      sourcePath: payload.sourcePath,
      timelineStartSecs: startSecs,
    });
  };

  const beginTrim = (e: React.PointerEvent, clip: Clip, mode: "trim-l" | "trim-r") => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    setTrim({
      clipId: clip.id,
      mode,
      originStart: clipStartSecs(clip),
      originEnd: clipEndSecs(clip),
    });
    trimPointerId.current = e.pointerId;
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  const handleTrimMove = (e: React.PointerEvent) => {
    if (trimPointerId.current !== e.pointerId || !trim) return;
    const snap = pxToSecSnapped(e.movementX, pxPerSec);
    if (trim.mode === "trim-l") {
      const ns = Math.min(trim.originEnd - 0.1, Math.max(0, trim.originStart + snap));
      setTrimShape({ clipId: trim.clipId, shape: { left: toPx(ns, pxPerSec), width: toPx(trim.originEnd - ns, pxPerSec) } });
    } else {
      const ne = Math.max(trim.originStart + 0.1, trim.originEnd + snap);
      setTrimShape({ clipId: trim.clipId, shape: { left: toPx(trim.originStart, pxPerSec), width: toPx(ne - trim.originStart, pxPerSec) } });
    }
  };

  const handleTrimUp = (e: React.PointerEvent) => {
    if (trimPointerId.current !== e.pointerId || !trim) return;
    trimPointerId.current = null;
    const t = trim;
    const snap = pxToSecSnapped(e.movementX, pxPerSec);
    const st = getLatestState();
    const ctx = st && st.currentId ? st : state;
    if (t.mode === "trim-l") {
      const ns = Math.min(t.originEnd - 0.1, Math.max(0, t.originStart + snap));
      if (Math.abs(ns - t.originStart) >= 0.05) void trimClip(dispatch, ctx, { clipId: t.clipId, newStart: secsToRatString(ns) });
    } else {
      const ne = Math.max(t.originStart + 0.1, t.originEnd + snap);
      if (Math.abs(ne - t.originEnd) >= 0.05) void trimClip(dispatch, ctx, { clipId: t.clipId, newEnd: secsToRatString(ne) });
    }
    setTrim(null);
    setTrimShape(null);
  };

  const cancelTrim = () => {
    trimPointerId.current = null;
    setTrim(null);
    setTrimShape(null);
  };

  return (
    <div
      className={`timeline-track${locked ? " timeline-track--locked" : ""}${muted ? " timeline-track--muted" : ""}${hidden ? " timeline-track--hidden" : ""}`}
    >
      <div className="timeline-track__label" title={displayName}>
        <span className={`timeline-track__kind timeline-track__kind--${isAudio ? "audio" : "video"}`}>
          {isAudio ? <Music size={11} /> : <Film size={11} />}
        </span>
        <span className="timeline-track__name">{displayName}</span>
        <div className="track-toggles">
          <button
            className={`track-toggle${locked ? " track-toggle--on" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              handleToggle("locked", !locked);
            }}
            aria-label={locked ? `解锁轨道 ${track.id}` : `锁定轨道 ${track.id}`}
            aria-pressed={locked}
            title={locked ? "解锁轨道" : "锁定轨道"}
          >
            {locked ? <Lock size={13} /> : <Unlock size={13} />}
          </button>
          <button
            className={`track-toggle${muted ? " track-toggle--on" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              handleToggle("muted", !muted);
            }}
            aria-label={muted ? `取消静音 ${track.id}` : `静音轨道 ${track.id}`}
            aria-pressed={muted}
            title={muted ? "取消静音" : "静音轨道"}
          >
            {muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
          </button>
          <button
            className={`track-toggle${hidden ? " track-toggle--on" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              handleToggle("visible", !hidden);
            }}
            aria-label={hidden ? `显示轨道 ${track.id}` : `隐藏轨道 ${track.id}`}
            aria-pressed={hidden}
            title={hidden ? "显示轨道" : "隐藏轨道"}
          >
            {hidden ? <EyeOff size={13} /> : <Eye size={13} />}
          </button>
        </div>
      </div>
      <div
        className={`timeline-track__lane ${laneFocused ? "timeline-track__lane--focused" : ""} ${dropOver ? "timeline-track__lane--drop" : ""}`}
        data-track-id={track.id}
        data-track-kind={track.kind}
        onClick={() => {
          if (state.selection?.trackId === track.id) dispatch({ type: "SELECTION_SET", selection: null });
          else dispatch({ type: "SELECTION_SET", selection: { trackId: track.id } });
        }}
        onPointerMove={(e) => {
          dragApi.move(e);
          handleTrimMove(e);
        }}
        onPointerUp={(e) => {
          dragApi.finish(e);
          handleTrimUp(e);
        }}
        onPointerCancel={() => {
          dragApi.cancel();
          cancelTrim();
        }}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {track.clips.length === 0 ? <span className="timeline-track__lane-empty">空轨道</span> : null}
        {track.clips.map((clip) => (
          <ClipBlock
            key={clip.id}
            clip={clip}
            isAudio={isAudio}
            pxPerSec={pxPerSec}
            locked={locked}
            selected={state.selection?.clipId === clip.id && state.selection?.trackId === track.id}
            trimOverlay={trimShape?.clipId === clip.id ? trimShape.shape : undefined}
            onDragStart={(e, mode) => {
              if (mode === "move") dragApi.begin(e, clip, track, "move", pxPerSec);
              else beginTrim(e, clip, mode);
            }}
            onContextMenu={(e) => {
              if (locked) return; // 锁定轨道：禁止片段编辑（含右键菜单）
              e.preventDefault();
              e.stopPropagation();
              setMenuTarget({ x: e.clientX, y: e.clientY, clipId: clip.id, clip, trackId: track.id, playheadSecs: state.playhead });
            }}
            onCutClick={(e) => handleCutClick(e, clip)}
          />
        ))}
      </div>
      {menuTarget ? (
        <ClipContextMenu target={menuTarget} onClose={() => setMenuTarget(null)} dispatch={dispatch} />
      ) : null}
    </div>
  );
}

function ClipBlock({
  clip,
  isAudio,
  pxPerSec,
  locked,
  selected,
  trimOverlay,
  onDragStart,
  onContextMenu,
  onCutClick,
}: {
  clip: Clip;
  isAudio: boolean;
  pxPerSec: number;
  locked?: boolean;
  selected: boolean;
  trimOverlay?: TrimShape;
  onDragStart: (e: React.PointerEvent, mode: "move" | "trim-l" | "trim-r") => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onCutClick: (e: React.MouseEvent) => void;
}) {
  const baseLeft = toPx(clipStartSecs(clip), pxPerSec);
  const durSecs = clipEndSecs(clip) - clipStartSecs(clip);
  const baseWidth = toPx(durSecs, pxPerSec);
  const left = trimOverlay?.left ?? baseLeft;
  const width = trimOverlay ? Math.max(trimOverlay.width, 8) : Math.max(baseWidth, 8);
  const name = sourceBasename(clip.assetRef.sourcePath);

  return (
    <div
      className={`clip-block clip-block--${isAudio ? "audio" : "video"} ${selected ? "clip-block--selected" : ""}`}
      style={{ left, width }}
      onClick={(e) => {
        e.stopPropagation();
        onCutClick(e);
      }}
      onContextMenu={onContextMenu}
      onPointerDown={(e) => {
        if (!locked) onDragStart(e, "move");
      }}
      title={`${name} · ${durSecs.toFixed(1)}s`}
    >
      <div className="clip-block__name">{name}</div>
      <div className="clip-block__meta">{durSecs.toFixed(1)}s</div>
      <span
        className="clip-block__drag clip-block__drag--l"
        onPointerDown={(e) => {
          e.stopPropagation();
          if (!locked) onDragStart(e, "trim-l");
        }}
      />
      <span
        className="clip-block__drag clip-block__drag--r"
        onPointerDown={(e) => {
          e.stopPropagation();
          if (!locked) onDragStart(e, "trim-r");
        }}
      />
    </div>
  );
}