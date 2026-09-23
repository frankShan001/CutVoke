/** 播放器/预览面板（中区）：按需播放短时间窗 + 图片轮询降级。
    主路径：<video src=/api/v1/projects/{id}/preview-window>（含音频、空白、效果）。
    播放/暂停/停止操作 video；播放头拖动 → seek；时间码读 video.currentTime。
    降级：preview-media 失败（空工程/渲染失败）→ 回退 preview-frame 图片轮询。 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Play, Pause, Square, SkipBack, ZoomIn, ZoomOut, ChevronLeft, ChevronRight, Scissors, Repeat, Flag } from "lucide-react";
import { useEditor } from "../store/editor";
import { splitClip } from "../store/clipEdit";
import { CaptionCanvasOverlay } from "./CaptionCanvasOverlay";
import { fpsToStep } from "../hooks/useKeyboardShortcuts";
import { fmtTime, fmtTimePrecise } from "./timeline/util";
import { findClipById, hasVideoCoverageAt, playbackEndSecs, rationalSeconds, secsToRational, PLAYER_RATIOS } from "./playerUtils";
import { RatioIcon } from "./playerIcons";
import { usePreviewFrame } from "./usePreviewFrame";

const FIT = "fit" as const;
type ZoomMode = typeof FIT | number;
type PlayMode = "video" | "image";
const PREVIEW_WINDOW_SECONDS = 8;

function contentToken(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

export function Player() {
  const { state, dispatch } = useEditor();
  const { project, currentId, playhead } = state;
  const [mode, setMode] = useState<PlayMode>("video");
  const [playing, setPlaying] = useState(false);
  const [videoTime, setVideoTime] = useState(0);
  const [zoom, setZoom] = useState<ZoomMode>(FIT);
  const [ratio, setRatio] = useState<[number, number]>([16, 9]);
  const [buffering, setBuffering] = useState(false);
  const [loopEnabled, setLoopEnabled] = useState(false);
  const [loopIn, setLoopIn] = useState<number | null>(null);
  const [loopOut, setLoopOut] = useState<number | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const playingRef = useRef(playing);
  playingRef.current = playing;
  const playheadRef = useRef(playhead);
  playheadRef.current = playhead;
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;
  const pauseStatusRef = useRef("已暂停");
  const suppressSeek = useRef(false);
  const reportedVideoTimeRef = useRef(0);
  const loopRef = useRef({ enabled: false, in: null as number | null, out: null as number | null });
  loopRef.current = { enabled: loopEnabled, in: loopIn, out: loopOut };

  const tracks = project?.sequence.tracks || [];
  const selectedTrackLocked = !!tracks.find((track) => track.id === state.selection?.trackId)?.locked;
  const mediaEndSecs = useMemo(() => playbackEndSecs(tracks), [tracks]);
  const windowIndex = Math.floor(Math.min(Math.max(0, playhead), Math.max(0, mediaEndSecs - 0.001)) / PREVIEW_WINDOW_SECONDS);
  const windowStart = windowIndex * PREVIEW_WINDOW_SECONDS;
  const windowEnd = Math.min(mediaEndSecs, windowStart + PREVIEW_WINDOW_SECONDS);
  // 基础预览视频不包含字幕：字幕修改只更新 Canvas 叠层，不能因 revision
  // 改变而重新挂载 <video>、打断当前播放。画面/音频轨道变化才换 key 重载视频。
  const previewContentKey = useMemo(() => {
    if (!project) return "";
    return JSON.stringify({ ...project.sequence, captions: [] });
  }, [project]);
  const previewTimelineToken = useMemo(() => contentToken(previewContentKey), [previewContentKey]);
  // 服务端仅在有「可见视频片段」时才渲染 preview-media（空时间线返回 422 EMPTY_TIMELINE）。
  // 客户端对齐该条件：无可见视频片段、或工程/revision 未就绪时，不构造 URL → 不渲染 <video>、不发请求。
  const hasRenderable = tracks.some(
    (t) => t.kind === "video" && t.visible !== false && (t.clips || []).some((c) => !c.hidden),
  );
  const previewTime = mode === "video" && playing ? videoTime : playhead;
  const hasVideoCoverage = hasVideoCoverageAt(tracks, previewTime);
  const videoUrl =
    currentId && state.revision && hasRenderable
      ? `/api/v1/projects/${encodeURIComponent(currentId)}/preview-window?index=${windowIndex}&timeline=${previewTimelineToken}`
      : "";
  const endPlay = useCallback(() => {
    setPlaying(false);
    pauseStatusRef.current = "播放结束";
    dispatchRef.current({ type: "STATUS_SET", severity: "ok", text: "播放结束" });
  }, []);

  const { frameUrl, frameState, scheduleFrame, reset: resetFrames } = usePreviewFrame({
    projectId: currentId,
    active: mode === "image",
    playing,
    totalSecs: mediaEndSecs,
    playheadRef,
    dispatchRef,
    onEnded: endPlay,
  });

  // video 就绪 / 失败 → 切换主/降级模式
  const onVideoLoaded = useCallback(() => {
    setMode("video");
    const v = videoRef.current;
    if (!v || Number.isNaN(v.duration) || v.duration <= 0) return;
    const target = Math.min(windowEnd, Math.max(windowStart, playheadRef.current));
    const localTarget = Math.max(0, target - windowStart);
    if (Math.abs(v.currentTime - localTarget) > 0.03) {
      try {
        v.currentTime = localTarget;
      } catch {
        /* keep the decoded position if the browser rejects an early metadata seek */
      }
    }
    setVideoTime(target);
    reportedVideoTimeRef.current = target;
    if (playingRef.current && target < mediaEndSecs - 0.01) void v.play();
  }, [mediaEndSecs, windowStart, windowEnd]);
  const onVideoError = useCallback(() => {
    setMode("image");
    scheduleFrame(playheadRef.current);
  }, [scheduleFrame]);
  const onVideoEnded = useCallback(() => {
    if (windowEnd < mediaEndSecs - 0.01) {
      setVideoTime(windowEnd);
      dispatchRef.current({ type: "PLAYHEAD_SET", t: windowEnd });
      return;
    }
    setVideoTime(mediaEndSecs);
    dispatchRef.current({ type: "PLAYHEAD_SET", t: mediaEndSecs });
    endPlay();
  }, [endPlay, mediaEndSecs, windowEnd]);

  const onTimeUpdate = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const absoluteTime = windowStart + v.currentTime;
    // A native preview can be stale or longer than the current editable sequence.
    // The editor's content boundary is authoritative for both playback and scrubbing.
    const lp = loopRef.current;
    // 循环区间：播放越过出点 → 回跳到入点（仅时视频主路径）
    const loopEnd = lp.in != null && lp.out != null
      ? Math.min(lp.out, mediaEndSecs)
      : null;
    if (mode === "video" && !v.paused && lp.enabled && lp.in != null && loopEnd != null && loopEnd > lp.in) {
      if (absoluteTime >= loopEnd) {
        if (lp.in >= windowStart && lp.in < windowEnd) v.currentTime = lp.in - windowStart;
        setVideoTime(lp.in);
        dispatchRef.current({ type: "PLAYHEAD_SET", t: lp.in });
        return;
      }
    }
    if (!v.paused && mediaEndSecs > 0 && absoluteTime >= mediaEndSecs - 0.01) {
      try {
        v.currentTime = mediaEndSecs - windowStart;
      } catch {
        /* keep the current decoded frame if the media element rejects the exact end */
      }
      setVideoTime(mediaEndSecs);
      dispatchRef.current({ type: "PLAYHEAD_SET", t: mediaEndSecs });
      pauseStatusRef.current = "播放结束";
      v.pause();
      endPlay();
      return;
    }
    const boundedTime = Math.min(mediaEndSecs, Math.max(0, absoluteTime));
    setVideoTime(boundedTime);
    if (playingRef.current && Math.abs(playheadRef.current - reportedVideoTimeRef.current) > 0.25) return;
    reportedVideoTimeRef.current = boundedTime;
    if (playingRef.current || Math.abs(boundedTime - playheadRef.current) > 0.5) {
      suppressSeek.current = true;
      dispatchRef.current({ type: "PLAYHEAD_SET", t: boundedTime });
      suppressSeek.current = false;
    }
  }, [endPlay, mediaEndSecs, mode, windowStart, windowEnd]);
  const onPlay = useCallback(() => {
    pauseStatusRef.current = "已暂停";
    setPlaying(true);
    setBuffering(false);
    dispatchRef.current({ type: "STATUS_SET", severity: "ok", text: "播放中" });
  }, []);
  const onPause = useCallback(() => {
    if (videoRef.current?.ended && windowEnd < mediaEndSecs - 0.01) return;
    setPlaying(false);
    dispatchRef.current({ type: "STATUS_SET", severity: "ok", text: pauseStatusRef.current });
    pauseStatusRef.current = "已暂停";
  }, [windowEnd, mediaEndSecs]);
  const onWaiting = useCallback(() => setBuffering(true), []);
  const onCanPlay = useCallback(() => setBuffering(false), []);

  // 外部播放头变化（时间线拖动/逐帧）→ seek video
  useEffect(() => {
    const boundedPlayhead = Math.min(mediaEndSecs, Math.max(0, playhead));
    if (Math.abs(boundedPlayhead - playhead) > 0.001) {
      dispatchRef.current({ type: "PLAYHEAD_SET", t: boundedPlayhead });
    }
  }, [playhead, mediaEndSecs]);

  useEffect(() => {
    const v = videoRef.current;
    if (mode !== "video" || !v || suppressSeek.current) return;
    const boundedPlayhead = Math.min(mediaEndSecs, Math.max(0, playhead));
    if (Math.abs(boundedPlayhead - playhead) > 0.001) {
      dispatchRef.current({ type: "PLAYHEAD_SET", t: boundedPlayhead });
    }
    setVideoTime(boundedPlayhead);
    const externalSeek = Math.abs(boundedPlayhead - reportedVideoTimeRef.current) > 0.25;
    if ((!playingRef.current || externalSeek) &&
        boundedPlayhead >= windowStart && boundedPlayhead <= windowEnd &&
        Math.abs(v.currentTime - (boundedPlayhead - windowStart)) > 0.03) {
      try {
        v.currentTime = Math.max(0, boundedPlayhead - windowStart);
        reportedVideoTimeRef.current = boundedPlayhead;
      } catch {
        /* ignore */
      }
    }
  }, [playhead, mediaEndSecs, mode, windowStart]);

  // timeupdate 在多数浏览器只有约 4Hz；播放时逐动画帧同步共享播放头，
  // 让下方标尺与实际解码位置保持同一时间源。
  useEffect(() => {
    if (!playing || mode !== "video") return;
    let frameId = 0;
    const sync = () => {
      const v = videoRef.current;
      if (!v || v.paused) return;
      const current = Math.min(mediaEndSecs, Math.max(0, windowStart + v.currentTime));
      setVideoTime(current);
      if (Math.abs(playheadRef.current - reportedVideoTimeRef.current) > 0.25) {
        frameId = window.requestAnimationFrame(sync);
        return;
      }
      reportedVideoTimeRef.current = current;
      if (Math.abs(current - playheadRef.current) > 1 / 240) {
        dispatchRef.current({ type: "PLAYHEAD_SET", t: current });
      }
      frameId = window.requestAnimationFrame(sync);
    };
    frameId = window.requestAnimationFrame(sync);
    return () => window.cancelAnimationFrame(frameId);
  }, [playing, mode, mediaEndSecs, windowStart]);

  // A one-byte Range request prepares the next short window in the server
  // cache while the current window is playing.
  useEffect(() => {
    if (!playing || !currentId || !hasRenderable || windowEnd >= mediaEndSecs - 0.01) return;
    const controller = new AbortController();
    const nextUrl = `/api/v1/projects/${encodeURIComponent(currentId)}/preview-window?index=${windowIndex + 1}&timeline=${previewTimelineToken}`;
    void fetch(nextUrl, { headers: { Range: "bytes=0-0" }, signal: controller.signal }).catch(() => {});
    return () => controller.abort();
  }, [playing, currentId, hasRenderable, windowEnd, mediaEndSecs, windowIndex, previewTimelineToken]);

  // 空格快捷键 → 播放/暂停
  const togglePlayRef = useRef<() => void>(() => {});
  useEffect(() => {
    const onToggle = () => togglePlayRef.current();
    window.addEventListener("cutvoke:toggle-play", onToggle);
    return () => window.removeEventListener("cutvoke:toggle-play", onToggle);
  }, []);

  // 切工程：重置状态与 video
  useEffect(() => {
    setMode("video");
    resetFrames();
    setPlaying(false);
    setVideoTime(0);
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.currentTime = 0;
    }
  }, [currentId, resetFrames]);

  // 基础画面变化后重新尝试 video 模式；字幕更新不应让已降级的预览反复重试。
  useEffect(() => {
    if (videoUrl) setMode("video");
  }, [videoUrl, previewContentKey]);

  const togglePlay = () => {
    if (!currentId || !project) return;
    if (!hasRenderable) {
      dispatch({
        type: "STATUS_SET",
        severity: "warn",
        text: "请先将视频或图片素材放入可见轨道",
      });
      return;
    }
    const v = videoRef.current;
    if (mode === "video" && v) {
      if (v.paused) {
        if (mediaEndSecs <= 0) return;
        if (playhead >= mediaEndSecs - 0.01 || windowStart + v.currentTime >= mediaEndSecs - 0.01) {
          if (windowStart === 0) v.currentTime = 0;
          setVideoTime(0);
          dispatch({ type: "PLAYHEAD_SET", t: 0 });
          if (windowStart !== 0) {
            setPlaying(true);
            return;
          }
        }
        void v.play();
      } else {
        pauseStatusRef.current = "已暂停";
        v.pause();
      }
      return;
    }
    if (playing) {
      setPlaying(false);
      dispatch({ type: "STATUS_SET", severity: "ok", text: "已暂停" });
    }
    else {
      if (mediaEndSecs <= 0) return;
      if (playhead >= mediaEndSecs - 0.01) dispatch({ type: "PLAYHEAD_SET", t: 0 });
      setPlaying(true);
      dispatch({ type: "STATUS_SET", severity: "ok", text: "播放中" });
    }
  };
  togglePlayRef.current = togglePlay;

  const stop = () => {
    setPlaying(false);
    const v = videoRef.current;
    if (mode === "video" && v) {
      if (!v.paused) pauseStatusRef.current = "已回到开头";
      v.pause();
      v.currentTime = 0;
    }
    dispatch({ type: "PLAYHEAD_SET", t: 0 });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已回到开头" });
  };

  const stepFrame = (dir: -1 | 1) => {
    if (!currentId) return;
    setPlaying(false);
    const step = fpsToStep(project?.sequence.fps);
    const next = Math.min(mediaEndSecs, Math.max(0, playhead + dir * step));
    dispatch({ type: "PLAYHEAD_SET", t: next });
    const v = videoRef.current;
    if (mode === "video" && v) {
      if (!v.paused) pauseStatusRef.current = "单帧预览";
      v.pause();
      setVideoTime(next);
      if (next >= windowStart && next < windowEnd) {
        try {
          v.currentTime = next - windowStart;
        } catch {
          /* ignore */
        }
      }
    }
    dispatch({ type: "STATUS_SET", severity: "ok", text: "单帧预览" });
  };

  const handleSplit = () => {
    if (state.editLock) return;
    const clipId = state.selection?.clipId;
    if (!currentId || !clipId) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "请先选中要分割的片段" });
      return;
    }
    if (selectedTrackLocked) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "所选片段所在轨道已锁定，解锁后才能分割" });
      return;
    }
    const clip = findClipById(state, clipId);
    if (!clip) return;
    const cs = rationalSeconds(clip.timelineStart);
    const ce = rationalSeconds(clip.timelineEnd);
    const at = mode === "video" ? videoTime : playhead;
    if (at > cs + 0.01 && at < ce - 0.01) {
      void splitClip(dispatch, state, { clipId, time: secsToRational(at) });
    } else {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "播放头需在片段内部才能分割" });
    }
  };

  const cycleRatio = () =>
    setRatio((r) => {
      const idx = PLAYER_RATIOS.findIndex((x) => x.wh[0] === r[0] && x.wh[1] === r[1]);
      return PLAYER_RATIOS[(idx + 1) % PLAYER_RATIOS.length].wh;
    });

  const frameStyle: React.CSSProperties =
    zoom === FIT
      ? { width: "auto", height: "auto", maxWidth: "calc(100% - 16px)", maxHeight: "calc(100% - 16px)", aspectRatio: `${ratio[0]}/${ratio[1]}` }
      : { width: `${640 * zoom}px`, height: `${360 * zoom}px`, aspectRatio: "auto" };

  const shownTime = mode === "video" ? videoTime : playhead;
  const totalDisplay = mediaEndSecs;
  const ratioLabel = PLAYER_RATIOS.find((x) => x.wh[0] === ratio[0] && x.wh[1] === ratio[1])?.label || "16:9";

  return (
    <section className="player">
      <div className="player__stage">
        <div className="player__canvas-frame" style={frameStyle} aria-label="预览画面">
          {!videoUrl ? (
            <div className="player__placeholder">暂无可见视频片段：导入素材并拖到时间线后此处显示预览</div>
          ) : mode === "video" ? (
            <>
              <video
                ref={videoRef}
                key={videoUrl}
                src={videoUrl}
                className="player__video"
                style={{ visibility: hasVideoCoverage ? "visible" : "hidden" }}
                playsInline
                onLoadedMetadata={onVideoLoaded}
                onLoadedData={onVideoLoaded}
                onError={onVideoError}
                onTimeUpdate={onTimeUpdate}
                onPlay={onPlay}
                onPause={onPause}
                onEnded={onVideoEnded}
                onWaiting={onWaiting}
                onCanPlay={onCanPlay}
              />
              {buffering ? <div className="cv-loading">缓冲中…</div> : null}
            </>
          ) : frameState === "ok" && frameUrl ? (
            <img src={frameUrl} alt={`t=${playhead.toFixed(3)} 预览帧`} draggable={false} />
          ) : frameState === "empty" ? (
            <div className="player__placeholder">该时刻无片段（黑帧）</div>
          ) : frameState === "loading" ? (
            <div className="cv-loading">加载预览…</div>
          ) : (
            <div className="player__placeholder">预览不可用（无片段）</div>
          )}
          {videoUrl && mode !== "video" ? <span className="player__mode-badge">预览降级：图片</span> : null}
          <CaptionCanvasOverlay />
        </div>
      </div>

      <div className="player-controls">
        <button className="player__iconbtn" onClick={stop} title="回到开头" aria-label="回到开头">
          <SkipBack size={16} />
        </button>
        <button className="player__play" onClick={togglePlay} disabled={!currentId} aria-label={playing ? "暂停" : "播放"} aria-pressed={playing} aria-keyshortcuts="Space" title={playing ? "暂停（空格）" : "播放（空格）"}>
          {playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <button className="player__iconbtn" onClick={stop} disabled={!currentId} title="停止（归零）" aria-label="停止">
          <Square size={15} />
        </button>
        <button className="player__iconbtn" onClick={() => stepFrame(-1)} disabled={!currentId} title="上一帧" aria-label="上一帧">
          <ChevronLeft size={15} />
        </button>
        <span className="player-controls__time cv-mono">
          {fmtTimePrecise(shownTime)} / {fmtTime(totalDisplay)}
        </span>
        <button className="player__iconbtn" onClick={() => stepFrame(1)} disabled={!currentId} title="下一帧" aria-label="下一帧">
          <ChevronRight size={15} />
        </button>
        <button
          className={`player__iconbtn player-controls__loop ${loopEnabled ? "player__iconbtn--on" : ""}`}
          onClick={() => setLoopEnabled((v) => !v)}
          disabled={!currentId}
          title="循环播放区间"
          aria-label="循环播放区间"
          aria-pressed={loopEnabled}
        >
          <Repeat size={14} />
        </button>
        <button className="player__iconbtn player-controls__loop" onClick={() => setLoopIn(shownTime)} disabled={!currentId} title="设循环入点" aria-label="设循环入点">
          <Flag size={14} />
        </button>
        <button className="player__iconbtn player-controls__loop" onClick={() => setLoopOut(shownTime)} disabled={!currentId} title="设循环出点" aria-label="设循环出点">
          <Flag size={14} className="player__icon-flag-out" />
        </button>
        {loopIn != null && loopOut != null ? (
          <span className="cv-mono player-controls__loop" style={{ fontSize: 11, color: "var(--text-dim)" }}>
            循环 {fmtTime(loopIn)}–{fmtTime(loopOut)}
          </span>
        ) : null}
        <button
          className="player__iconbtn"
          onClick={handleSplit}
          disabled={!currentId || !state.selection?.clipId || selectedTrackLocked || !!state.editLock}
          title={selectedTrackLocked ? "所选片段所在轨道已锁定，解锁后才能分割" : "分割选中片段"}
          aria-label="分割选中片段"
        >
          <Scissors size={15} />
        </button>
        <span style={{ flex: 1 }} />
        <button className="player__iconbtn player-controls__view" onClick={cycleRatio} title="画布比例（仅预览显示）" aria-label="切换画布比例">
          <RatioIcon r={ratio} />
          <span className="player__zoom-val player__ratio-val">{ratioLabel}</span>
        </button>
        <span className="player__zoom-bar player-controls__view">
          <button className="player__iconbtn" onClick={() => setZoom((z) => (z === FIT ? 0.5 : z === 0.5 ? 1.0 : z === 1.0 ? 1.5 : 0.5))} title="缩小" aria-label="缩小">
            <ZoomOut size={14} />
          </button>
          <span className="player__zoom-val">{zoom === FIT ? "适应" : `${Math.round(zoom * 100)}%`}</span>
          <button className="player__iconbtn" onClick={() => setZoom((z) => (z === FIT ? 0.5 : z === 0.5 ? 1.0 : z === 1.0 ? 1.5 : FIT))} title="放大" aria-label="放大">
            <ZoomIn size={14} />
          </button>
        </span>
      </div>
    </section>
  );
}
