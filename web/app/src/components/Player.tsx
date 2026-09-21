/** 播放器/预览面板（中区）：真实媒体播放（主路径 video）+ 图片轮询降级。
    主路径：<video src=/api/v1/projects/{id}/preview-media>（含音频、绝对时间空白、效果）。
    播放/暂停/停止操作 video；播放头拖动 → seek；时间码读 video.currentTime。
    降级：preview-media 失败（空工程/渲染失败）→ 回退 preview-frame 图片轮询。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Pause, Square, SkipBack, ZoomIn, ZoomOut, ChevronLeft, ChevronRight, Scissors, Repeat, Flag } from "lucide-react";
import { useEditor } from "../store/editor";
import { splitClip } from "../store/clipEdit";
import { CaptionCanvasOverlay } from "./CaptionCanvasOverlay";
import { fpsToStep } from "../hooks/useKeyboardShortcuts";
import { fmtTime, timelineLengthSecs } from "./timeline/util";
import { findClipById, rationalSeconds, secsToRational, videoDuration, videoDurationSecs, PLAYER_RATIOS } from "./playerUtils";
import { RatioIcon } from "./playerIcons";
import { usePreviewFrame } from "./usePreviewFrame";

const FIT = "fit" as const;
type ZoomMode = typeof FIT | number;
type PlayMode = "video" | "image";

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
  const suppressSeek = useRef(false);
  const loopRef = useRef({ enabled: false, in: null as number | null, out: null as number | null });
  loopRef.current = { enabled: loopEnabled, in: loopIn, out: loopOut };

  const tracks = project?.sequence.tracks || [];
  const totalSecs = project ? timelineLengthSecs(tracks) : 0;
  // 服务端仅在有「可见视频片段」时才渲染 preview-media（空时间线返回 422 EMPTY_TIMELINE）。
  // 客户端对齐该条件：无可见视频片段、或工程/revision 未就绪时，不构造 URL → 不渲染 <video>、不发请求。
  const hasRenderable = tracks.some(
    (t) => t.kind === "video" && (t.clips || []).some((c) => !c.hidden),
  );
  const videoUrl =
    currentId && state.revision && hasRenderable
      ? `/api/v1/projects/${currentId}/preview-media`
      : "";
  const endPlay = useCallback(() => setPlaying(false), []);

  const { frameUrl, frameState, scheduleFrame, reset: resetFrames } = usePreviewFrame({
    projectId: currentId,
    active: mode === "image",
    playing,
    totalSecs,
    playheadRef,
    dispatchRef,
    onEnded: endPlay,
  });

  // video 就绪 / 失败 → 切换主/降级模式
  const onVideoLoaded = useCallback(() => {
    setMode("video");
    const v = videoRef.current;
    if (v && !Number.isNaN(v.duration) && v.duration > 0) setVideoTime(v.currentTime);
  }, []);
  const onVideoError = useCallback(() => {
    setMode("image");
    scheduleFrame(playheadRef.current);
  }, [scheduleFrame]);

  const onTimeUpdate = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const lp = loopRef.current;
    // 循环区间：播放越过出点 → 回跳到入点（仅时视频主路径）
    if (mode === "video" && lp.enabled && lp.in != null && lp.out != null && lp.out > lp.in) {
      if (v.currentTime >= lp.out) {
        v.currentTime = lp.in;
        setVideoTime(lp.in);
        dispatchRef.current({ type: "PLAYHEAD_SET", t: lp.in });
        return;
      }
    }
    setVideoTime(v.currentTime);
    if (playingRef.current || Math.abs(v.currentTime - playheadRef.current) > 0.5) {
      suppressSeek.current = true;
      dispatchRef.current({ type: "PLAYHEAD_SET", t: v.currentTime });
      suppressSeek.current = false;
    }
  }, [mode]);
  const onPlay = useCallback(() => {
    setPlaying(true);
    setBuffering(false);
    dispatchRef.current({ type: "STATUS_SET", severity: "ok", text: "播放中" });
  }, []);
  const onPause = useCallback(() => setPlaying(false), []);
  const onWaiting = useCallback(() => setBuffering(true), []);
  const onCanPlay = useCallback(() => setBuffering(false), []);

  // 外部播放头变化（时间线拖动/逐帧）→ seek video
  useEffect(() => {
    const v = videoRef.current;
    if (mode !== "video" || !v || suppressSeek.current) return;
    if (!playingRef.current && Math.abs(v.currentTime - playhead) > 0.03) {
      try {
        v.currentTime = playhead;
      } catch {
        /* ignore */
      }
    }
  }, [playhead, mode]);

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

  // videoUrl（工程/内容变化 → 含 ?rev=）→ 重新尝试 video 模式。
  // 关键：避免 onError 一次降级后永久停在图片模式；内容变一次就重试一次。
  useEffect(() => {
    if (videoUrl) setMode("video");
  }, [videoUrl, state.revision]);

  const togglePlay = () => {
    if (!currentId || !project) return;
    const v = videoRef.current;
    if (mode === "video" && v) {
      if (v.paused) {
        if (Math.abs(v.currentTime - videoDuration(v)) < 0.05) v.currentTime = 0;
        void v.play();
      } else {
        v.pause();
      }
      return;
    }
    if (playing) setPlaying(false);
    else {
      if (playhead >= totalSecs - 0.05) dispatch({ type: "PLAYHEAD_SET", t: 0 });
      setPlaying(true);
    }
  };
  togglePlayRef.current = togglePlay;

  const stop = () => {
    setPlaying(false);
    const v = videoRef.current;
    if (mode === "video" && v) {
      v.pause();
      v.currentTime = 0;
    }
    dispatch({ type: "PLAYHEAD_SET", t: 0 });
  };

  const stepFrame = (dir: -1 | 1) => {
    if (!currentId) return;
    setPlaying(false);
    const step = fpsToStep(project?.sequence.fps);
    const next = Math.min(totalSecs, Math.max(0, playhead + dir * step));
    dispatch({ type: "PLAYHEAD_SET", t: next });
    const v = videoRef.current;
    if (mode === "video" && v) {
      try {
        v.currentTime = next;
      } catch {
        /* ignore */
      }
    }
  };

  const handleSplit = () => {
    const clipId = state.selection?.clipId;
    if (!currentId || !clipId) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "请先选中要分割的片段" });
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
  const totalDisplay = mode === "video" ? videoDurationSecs(videoRef.current) : totalSecs;
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
                key={state.revision}
                src={videoUrl}
                className="player__video"
                playsInline
                onLoadedMetadata={onVideoLoaded}
                onLoadedData={onVideoLoaded}
                onError={onVideoError}
                onTimeUpdate={onTimeUpdate}
                onPlay={onPlay}
                onPause={onPause}
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
        <button className="player__play" onClick={togglePlay} disabled={!currentId} aria-label={playing ? "暂停" : "播放"} title={playing ? "暂停" : "播放"}>
          {playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <button className="player__iconbtn" onClick={stop} disabled={!currentId} title="停止（归零）" aria-label="停止">
          <Square size={15} />
        </button>
        <button className="player__iconbtn" onClick={() => stepFrame(-1)} disabled={!currentId} title="上一帧" aria-label="上一帧">
          <ChevronLeft size={15} />
        </button>
        <span className="player-controls__time cv-mono">
          {fmtTime(shownTime)} / {mode === "video" ? fmtTime(totalDisplay) : fmtTime(totalSecs)}
        </span>
        <button className="player__iconbtn" onClick={() => stepFrame(1)} disabled={!currentId} title="下一帧" aria-label="下一帧">
          <ChevronRight size={15} />
        </button>
        <button
          className={`player__iconbtn ${loopEnabled ? "player__iconbtn--on" : ""}`}
          onClick={() => setLoopEnabled((v) => !v)}
          disabled={!currentId}
          title="循环播放区间"
          aria-label="循环播放区间"
        >
          <Repeat size={14} />
        </button>
        <button className="player__iconbtn" onClick={() => setLoopIn(shownTime)} disabled={!currentId} title="设循环入点" aria-label="设循环入点">
          <Flag size={14} />
        </button>
        <button className="player__iconbtn" onClick={() => setLoopOut(shownTime)} disabled={!currentId} title="设循环出点" aria-label="设循环出点">
          <Flag size={14} className="player__icon-flag-out" />
        </button>
        {loopIn != null && loopOut != null ? (
          <span className="cv-mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
            循环 {fmtTime(loopIn)}–{fmtTime(loopOut)}
          </span>
        ) : null}
        <button className="player__iconbtn" onClick={handleSplit} disabled={!currentId || !state.selection?.clipId} title="分割选中片段" aria-label="分割选中片段">
          <Scissors size={15} />
        </button>
        <span style={{ flex: 1 }} />
        <button className="player__iconbtn" onClick={cycleRatio} title="画布比例（仅预览显示）" aria-label="切换画布比例">
          <RatioIcon r={ratio} />
          <span className="player__zoom-val player__ratio-val">{ratioLabel}</span>
        </button>
        <span className="player__zoom-bar">
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
