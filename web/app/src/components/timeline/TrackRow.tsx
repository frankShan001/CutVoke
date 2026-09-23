/** 轨道泳道（配合共享拖拽系统）。
    - 片段整体拖动 → Timeline 全局拖拽（同轨 / 跨轨 = clip.move）。
    - 左/右缘拖拽 = clip.trim（本地 overlay + 释放提交，只改一端）。
    空白点击 = 取消选中；右键 = 上下文菜单。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRightLeft, Film, Music, Lock, Unlock, Volume2, VolumeX, Eye, EyeOff, Trash2 } from "lucide-react";
import type { Clip, Track } from "../../types/api";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { trimClip, insertClipFromDrop, moveClip, removeEffect, resolveInsertStart, splitClip, updateTrack } from "../../store/clipEdit";
import { ClipContextMenu, type CtxTarget } from "../ClipContextMenu";
import {
  clipStartSecs,
  clipEndSecs,
  frameDurationSecs,
  pxToSecSnapped,
  secsToFrameRatString,
  snapSecsToFrame,
  toPx,
} from "./util";
import { rationalToSecs } from "../../lib/rational";
import { sourceBasename } from "../../lib/media";
import { mediaFitsTrack, uploadFiles } from "../../lib/importMedia";
import { inferKind, type SessionAsset } from "../../lib/assetStore";
import { probeMedia } from "../../lib/mediaApi";
import { effectLabel, type EffectSpec } from "../../lib/effects";
import { isTransitionEffectId, transitionDurationSecs } from "../../lib/transitions";
import type { TimelineDragApi } from "./useTimelineDrag";

interface TrimState {
  clipId: string;
  mode: "trim-l" | "trim-r";
  originStart: number;
  originEnd: number;
  startClientX: number;
  sourceStart: number;
  speedAbs: number;
  sourceDuration: number | null;
}

interface TrimShape {
  left: number;
  width: number;
}

const sourceDurationCache = new Map<string, number | null>();

async function sourceDurationFor(path: string): Promise<number | null> {
  if (sourceDurationCache.has(path)) return sourceDurationCache.get(path) ?? null;
  const result = await probeMedia(path);
  const duration = result.kind === "ok" && result.data.duration > 0 ? result.data.duration : null;
  sourceDurationCache.set(path, duration);
  return duration;
}

export function TrackRow({
  track,
  displayName,
  pxPerSec,
  dragApi,
  effectSpecs,
}: {
  track: Track;
  /** 人类可读轨道名（如「视频轨 1」），替代内部 track.id。 */
  displayName: string;
  pxPerSec: number;
  dragApi: TimelineDragApi;
  effectSpecs: EffectSpec[];
}) {
  const { state, dispatch } = useEditor();
  const isAudio = track.kind === "audio";
  const frameRate = state.project?.sequence.fps;
  const frameDuration = frameDurationSecs(frameRate);
  // 轨道属性（后端 model 已有 locked/muted/visible；visible 缺省视为显示）
  const locked = !!track.locked;
  const agentLocked = !!state.editLock;
  const muted = !!track.muted;
  const hidden = track.visible === false;
  const [trim, setTrim] = useState<TrimState | null>(null);
  const [trimShape, setTrimShape] = useState<{ clipId: string; shape: TrimShape } | null>(null);
  const [menuTarget, setMenuTarget] = useState<CtxTarget | null>(null);
  const [dropOver, setDropOver] = useState(false);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const [sourceDurations, setSourceDurations] = useState<Record<string, number | null>>({});
  const trimPointerId = useRef<number | null>(null);

  const laneFocused = state.selection?.trackId === track.id && !state.selection?.clipId;

  const sourcePathsKey = track.clips.map((clip) => clip.assetRef.sourcePath).filter(Boolean).join("\u0000");
  useEffect(() => {
    let cancelled = false;
    const paths = [...new Set(sourcePathsKey.split("\u0000").filter(Boolean))];
    void Promise.all(paths.map(async (path) => [path, await sourceDurationFor(path)] as const)).then((entries) => {
      if (!cancelled) setSourceDurations(Object.fromEntries(entries));
    });
    return () => {
      cancelled = true;
    };
  }, [sourcePathsKey]);

  // 切换轨道属性：走 track.update 命令（缺省字段不变）
  const handleToggle = (field: "locked" | "muted" | "visible", value: boolean) => {
    const st = getLatestState() || state;
    void updateTrack(dispatch, st, { trackId: track.id, [field]: value });
  };

  // 切割模式：点击片段 → 在播放头处分割（若播放头在片段内）；否则选择
  const handleCutClick = (e: React.MouseEvent | React.KeyboardEvent, clip: Clip) => {
    if (state.toolMode === "cut") {
      e.preventDefault();
      e.stopPropagation();
      if (locked) {
        dispatch({ type: "STATUS_SET", severity: "warn", text: "此轨道已锁定，解锁后才能切割片段" });
        return;
      }
      const cs = rationalToSecs(clip.timelineStart);
      const ce = rationalToSecs(clip.timelineEnd);
      if (state.playhead > cs + frameDuration / 2 && state.playhead < ce - frameDuration / 2) {
        const st = getLatestState() || state;
        void splitClip(dispatch, st, { clipId: clip.id, time: secsToFrameRatString(state.playhead, frameRate) });
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
    e.dataTransfer.dropEffect = locked ? "none" : "copy";
    if (!locked) {
      if (!dropOver) setDropOver(true);
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      const raw = Math.max(0, (e.clientX - rect.left) / pxPerSec);
      const target = rationalToSecs(resolveInsertStart(getLatestState() || state, track.id, raw));
      setDropAt((previous) => previous != null && Math.abs(previous - target) < 1e-4 ? previous : target);
    }
  };

  const nudgeClip = (clip: Clip, direction: -1 | 1) => {
    if (locked || agentLocked) return;
    const latest = getLatestState() || state;
    const currentTrack = latest.project?.sequence.tracks.find((item) => item.id === track.id);
    const ordered = [...(currentTrack?.clips || [])].sort((a, b) => clipStartSecs(a) - clipStartSecs(b));
    const index = ordered.findIndex((item) => item.id === clip.id);
    const neighbor = ordered[index + direction];
    if (!neighbor) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "已经是该轨道的首段或末段" });
      return;
    }
    void moveClip(dispatch, latest, {
      clipId: clip.id,
      mode: "reorder",
      anchorClipId: neighbor.id,
      anchorPosition: direction < 0 ? "before" : "after",
    });
  };
  const handleDragLeave = (e: React.DragEvent) => {
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDropOver(false);
    setDropAt(null);
  };
  const handleDrop = (e: React.DragEvent) => {
    setDropOver(false);
    setDropAt(null);
    const files = e.dataTransfer.files;
    const hasFiles = !!files && files.length > 0;
    const raw = hasFiles ? "" : e.dataTransfer.getData("text/cutvoke-media");
    if (!hasFiles && !raw) return;
    e.preventDefault();
    e.stopPropagation();
    if (locked) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "此轨道已锁定，解锁后才能添加素材" });
      return;
    }
    // 计算 drop 时间线位置（px→sec）
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    // 轨道随 scroller 一起移动，rect.left 已反映横向滚动偏移。
    const startSecs = Math.max(0, (e.clientX - rect.left) / pxPerSec);
    const ctx = getLatestState() || state;
    if (hasFiles) {
      // OS 文件拖入：上传 → 依次插入本轨（按累计时长错位，避免重叠）
      let cursor = startSecs;
      void uploadFiles(
        Array.from(files),
        (media) => {
          if (!mediaFitsTrack(media.kind, track.kind)) {
            dispatch({
              type: "STATUS_SET",
              severity: "warn",
              text: media.kind === "audio"
                ? "音频素材不能放到视频轨；拖到下方空白处会自动创建音频轨"
                : "此素材不是音频，不能放到音频轨",
            });
            return;
          }
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
    let payload: { sourcePath: string; kind?: SessionAsset["kind"] };
    try {
      payload = JSON.parse(raw);
    } catch {
      return;
    }
    const mediaKind = payload.kind ?? inferKind(payload.sourcePath, false, false);
    if (!mediaFitsTrack(mediaKind, track.kind)) {
      dispatch({
        type: "STATUS_SET",
        severity: "warn",
        text: mediaKind === "audio"
          ? "音频素材不能放到视频轨；拖到下方空白处会自动创建音频轨"
          : "此素材不是音频，不能放到音频轨",
      });
      return;
    }
    void insertClipFromDrop(dispatch, ctx, {
      trackId: track.id,
      sourcePath: payload.sourcePath,
      timelineStartSecs: startSecs,
    });
  };

  const beginTrim = (e: React.PointerEvent, clip: Clip, mode: "trim-l" | "trim-r") => {
    if (agentLocked) return;
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    setTrim({
      clipId: clip.id,
      mode,
      originStart: clipStartSecs(clip),
      originEnd: clipEndSecs(clip),
      startClientX: e.clientX,
      sourceStart: rationalToSecs(clip.sourceStart),
      speedAbs: Math.max(0.000001, Math.abs(rationalToSecs(clip.speed || { num: "1", den: "1" }))),
      sourceDuration: sourceDurations[clip.assetRef.sourcePath] ?? null,
    });
    trimPointerId.current = e.pointerId;
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  const boundedTrimStart = (value: number, current: TrimState, minDuration: number) => {
    let minimum = 0;
    if (current.sourceDuration != null) {
      const maxTimelineDuration = Math.max(minDuration, (current.sourceDuration - current.sourceStart) / current.speedAbs);
      minimum = Math.max(0, current.originEnd - maxTimelineDuration);
    }
    return Math.min(current.originEnd - minDuration, Math.max(minimum, value));
  };

  const boundedTrimEnd = (value: number, current: TrimState, minDuration: number) => {
    let maximum = Number.POSITIVE_INFINITY;
    if (current.sourceDuration != null) {
      const maxTimelineDuration = Math.max(minDuration, (current.sourceDuration - current.sourceStart) / current.speedAbs);
      maximum = current.originStart + maxTimelineDuration;
    }
    return Math.min(maximum, Math.max(current.originStart + minDuration, value));
  };

  const handleTrimMove = (e: React.PointerEvent) => {
    if (trimPointerId.current !== e.pointerId || !trim) return;
    const deltaPx = e.clientX - trim.startClientX;
    if (Math.abs(deltaPx) < 4) return;
    const snap = pxToSecSnapped(deltaPx, pxPerSec, frameRate);
    if (trim.mode === "trim-l") {
      const ns = boundedTrimStart(snapSecsToFrame(trim.originStart + snap, frameRate), trim, frameDuration);
      setTrimShape({ clipId: trim.clipId, shape: { left: toPx(ns, pxPerSec), width: toPx(trim.originEnd - ns, pxPerSec) } });
    } else {
      const ne = boundedTrimEnd(snapSecsToFrame(trim.originEnd + snap, frameRate), trim, frameDuration);
      setTrimShape({ clipId: trim.clipId, shape: { left: toPx(trim.originStart, pxPerSec), width: toPx(ne - trim.originStart, pxPerSec) } });
    }
  };

  const handleTrimUp = (e: React.PointerEvent) => {
    if (trimPointerId.current !== e.pointerId || !trim) return;
    trimPointerId.current = null;
    const t = trim;
    if (agentLocked) {
      setTrim(null);
      setTrimShape(null);
      return;
    }
    const deltaPx = e.clientX - trim.startClientX;
    if (Math.abs(deltaPx) < 4) {
      setTrim(null);
      setTrimShape(null);
      return;
    }
    const snap = pxToSecSnapped(deltaPx, pxPerSec, frameRate);
    const st = getLatestState();
    const ctx = st && st.currentId ? st : state;
    const commitFrameRate = ctx.project?.sequence.fps ?? frameRate;
    const minDuration = frameDurationSecs(commitFrameRate);
    if (t.mode === "trim-l") {
      const requested = snapSecsToFrame(t.originStart + snap, commitFrameRate);
      const ns = boundedTrimStart(requested, t, minDuration);
      if (Math.abs(ns - t.originStart) >= minDuration / 2) {
        void trimClip(dispatch, ctx, { clipId: t.clipId, newStart: secsToFrameRatString(ns, commitFrameRate) });
      }
    } else {
      const requested = snapSecsToFrame(t.originEnd + snap, commitFrameRate);
      const ne = boundedTrimEnd(requested, t, minDuration);
      if (Math.abs(ne - t.originEnd) >= minDuration / 2) {
        void trimClip(dispatch, ctx, { clipId: t.clipId, newEnd: secsToFrameRatString(ne, commitFrameRate) });
      }
      if (t.sourceDuration != null && ne < requested - minDuration / 2) {
        dispatch({ type: "STATUS_SET", severity: "warn", text: "已到源素材末尾，不能继续延长" });
      }
    }
    setTrim(null);
    setTrimShape(null);
  };

  const cancelTrim = useCallback(() => {
    trimPointerId.current = null;
    setTrim(null);
    setTrimShape(null);
  }, []);

  useEffect(() => {
    if (agentLocked) {
      dragApi.cancel();
      cancelTrim();
    }
  }, [agentLocked, dragApi.cancel, cancelTrim]);

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
        data-track-locked={locked ? "true" : "false"}
        title={locked ? "此轨道已锁定；解锁后可编辑或添加素材" : undefined}
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
        {dropOver && dropAt !== null ? (
          <div
            className="timeline-track__insert-target"
            style={{ left: toPx(dropAt, pxPerSec) }}
            role="status"
            aria-label={`在 ${dropAt.toFixed(2)} 秒的片段边界插入素材`}
          >
            <span>插入</span>
          </div>
        ) : null}
        {track.clips.length === 0 ? <span className="timeline-track__lane-empty">空轨道</span> : null}
        {track.clips.map((clip) => (
          <ClipBlock
            key={clip.id}
            clip={clip}
            isAudio={isAudio}
            pxPerSec={pxPerSec}
            locked={locked}
            selected={state.selection?.clipId === clip.id && state.selection?.trackId === track.id && !state.selection?.effectId}
            trimOverlay={trimShape?.clipId === clip.id ? trimShape.shape : undefined}
            onDragStart={(e, mode) => {
              if (mode === "move") {
                if (state.toolMode !== "cut") dragApi.begin(e, clip, track, "move", pxPerSec);
              }
              else beginTrim(e, clip, mode);
            }}
            onContextMenu={(e) => {
              if (locked) {
                e.preventDefault();
                e.stopPropagation();
                dispatch({ type: "STATUS_SET", severity: "warn", text: "此轨道已锁定，解锁后才能编辑片段" });
                return;
              }
              e.preventDefault();
              e.stopPropagation();
              setMenuTarget({ x: e.clientX, y: e.clientY, clipId: clip.id, clip, trackId: track.id, playheadSecs: state.playhead });
            }}
            onCutClick={(e) => handleCutClick(e, clip)}
            onNudge={(direction) => nudgeClip(clip, direction)}
          />
        ))}
        {track.kind === "video"
          ? track.clips.flatMap((clip) =>
              (clip.effects || [])
                .filter((effect) => isTransitionEffectId(String(effect.effectId)))
                .map((effect, index) => {
                  const effectId = String(effect.effectId);
                  const label = effectLabel(effectSpecs, effectId);
                  const duration = transitionDurationSecs(effect);
                  const cutAt = clipStartSecs(clip);
                  const width = Math.max(56, toPx(duration, pxPerSec));
                  const selected =
                    state.selection?.trackId === track.id &&
                    state.selection?.clipId === clip.id &&
                    state.selection?.effectId === effectId &&
                    state.selection?.effectKind === "transition";
                  return (
                    <div
                      key={`${clip.id}-${effectId}-${index}`}
                      className={`transition-marker${selected ? " transition-marker--selected" : ""}`}
                      style={{ left: toPx(cutAt, pxPerSec) - width / 2, width }}
                      title={`${label} · ${duration.toFixed(1)}s · 单击选中，Delete 删除`}
                      onPointerDown={(event) => event.stopPropagation()}
                    >
                      <button
                        className="transition-marker__select"
                        type="button"
                        aria-pressed={selected}
                        aria-label={`${label} 转场，位于 ${cutAt.toFixed(2)} 秒`}
                        onClick={(event) => {
                          event.stopPropagation();
                          dispatch({
                            type: "SELECTION_SET",
                            selection: { trackId: track.id, clipId: clip.id, effectId, effectKind: "transition" },
                          });
                          dispatch({ type: "PLAYHEAD_SET", t: cutAt });
                        }}
                      >
                        <ArrowRightLeft size={11} />
                        <span className="transition-marker__name">{label}</span>
                      </button>
                      <button
                        className="transition-marker__delete"
                        type="button"
                        disabled={locked || agentLocked}
                        aria-label={`删除转场 ${label}`}
                        title={locked || agentLocked ? "轨道已锁定" : "删除转场"}
                        onClick={(event) => {
                          event.stopPropagation();
                          const latest = getLatestState() || state;
                          void removeEffect(dispatch, latest, { clipId: clip.id, effectId }).then((result) => {
                            if (result.ok) dispatch({ type: "SELECTION_SET", selection: null });
                          });
                        }}
                      >
                        <Trash2 size={9} />
                      </button>
                    </div>
                  );
                }),
            )
          : null}
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
  onNudge,
}: {
  clip: Clip;
  isAudio: boolean;
  pxPerSec: number;
  locked?: boolean;
  selected: boolean;
  trimOverlay?: TrimShape;
  onDragStart: (e: React.PointerEvent, mode: "move" | "trim-l" | "trim-r") => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onCutClick: (e: React.MouseEvent | React.KeyboardEvent) => void;
  onNudge: (direction: -1 | 1) => void;
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
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${name}，${durSecs.toFixed(1)} 秒，开始于 ${clipStartSecs(clip).toFixed(3)} 秒${selected ? "，已选中" : ""}；Alt 加左右方向键可与相邻片段交换顺序`}
      style={{ left, width }}
      onClick={(e) => {
        e.stopPropagation();
        onCutClick(e);
      }}
      onKeyDown={(e) => {
        if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
          e.preventDefault();
          e.stopPropagation();
          onNudge(e.key === "ArrowLeft" ? -1 : 1);
          return;
        }
        if (!e.repeat && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onCutClick(e);
        }
      }}
      onContextMenu={onContextMenu}
      onPointerDown={(e) => {
        if (!locked) onDragStart(e, "move");
      }}
      title={`${name} · ${durSecs.toFixed(1)}s · Alt+←/→ 调整顺序`}
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
