/** 播放器/预览面板（中区）：按需播放短时间窗 + 图片轮询降级。
    主路径：<video src=/api/v1/projects/{id}/preview-window>（含音频、空白、效果）。
    播放/暂停/停止操作 video；播放头拖动 → seek；时间码读 video.currentTime。
    降级：preview-media 失败（空工程/渲染失败）→ 回退 preview-frame 图片轮询。 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type SyntheticEvent } from "react";
import { Play, Pause, Square, SkipBack, ZoomIn, ZoomOut, ChevronLeft, ChevronRight, Scissors, Repeat, Flag, LoaderCircle } from "lucide-react";
import { useEditor } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { splitClip } from "../store/clipEdit";
import { CaptionCanvasOverlay } from "./CaptionCanvasOverlay";
import { MaskCanvasOverlay } from "./MaskCanvasOverlay";
import { CropCanvasOverlay } from "./CropCanvasOverlay";
import { StickerCanvasOverlay } from "./StickerCanvasOverlay";
import { TransformCanvasOverlay } from "./TransformCanvasOverlay";
import { fpsToStep } from "../hooks/useKeyboardShortcuts";
import { fmtTime, fmtTimePrecise } from "./timeline/util";
import { findClipById, hasVideoCoverageAt, playbackEndSecs, rationalSeconds, secsToRational, PLAYER_RATIOS } from "./playerUtils";
import { RatioIcon } from "./playerIcons";
import { usePreviewFrame } from "./usePreviewFrame";
import { usePreviewPoster } from "./usePreviewPoster";
import { useVideoHandoff } from "./useVideoHandoff";
import { usePreviewSegments } from "./usePreviewSegments";
import { PreviewVideoPool } from "./PreviewVideoPool";
import { getEffectCatalog } from "../lib/effects";
import { fetchPreviewFrame } from "../lib/mediaApi";
import { previewContentKey as projectPreviewKey } from "../lib/previewPreparation";

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

function formatPreviewError(message: string | null): string {
  if (!message) return "连续预览和单帧预览都无法使用。请检查素材文件和效果依赖后重试。";
  const missingSource = message.match(/source file missing for clip .+?:\s*(.+)$/i);
  if (missingSource) {
    return `素材文件不存在：${missingSource[1]}。请在素材库重新链接该文件后重试。`;
  }
  const missingLut = message.match(/imported LUT missing:\s*(.+)$/i);
  if (missingLut) {
    return `找不到已导入的 LUT 文件：${missingLut[1]}。请重新导入 LUT，或关闭对应效果后重试。`;
  }
  if (/minterpolate|frame interpolation/i.test(message)) {
    return "当前 FFmpeg 不支持运动估算慢动作补帧。请关闭该片段的补帧选项后重试。";
  }
  return `预览渲染失败：${message}`;
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
  const [readyVideoUrl, setReadyVideoUrl] = useState<string | null>(null);
  const [stageSize, setStageSize] = useState({ width: 640, height: 360 });
  const [loopEnabled, setLoopEnabled] = useState(false);
  const [showTitleSafeArea, setShowTitleSafeArea] = useState(false);
  const [showColorBefore, setShowColorBefore] = useState(false);
  const [backgroundPickerOpen, setBackgroundPickerOpen] = useState(false);
  const [backgroundDraft, setBackgroundDraft] = useState("#000000");
  const [backgroundSaving, setBackgroundSaving] = useState(false);
  const [sourceRestoreRevision, setSourceRestoreRevision] = useState(0);
  const [colorEffectIds, setColorEffectIds] = useState<Set<string>>(new Set());
  const [colorBeforeUrl, setColorBeforeUrl] = useState<string | null>(null);
  const [colorBeforeState, setColorBeforeState] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const colorBeforeUrlRef = useRef<string | null>(null);
  const [loopIn, setLoopIn] = useState<number | null>(null);
  const [loopOut, setLoopOut] = useState<number | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const playingRef = useRef(playing);
  playingRef.current = playing;
  const scrubbingRef = useRef(state.scrubbing);
  scrubbingRef.current = state.scrubbing;
  const scrubPausedVideoRef = useRef<HTMLVideoElement | null>(null);
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
  // 基础预览视频不包含字幕：字幕修改只更新 Canvas 叠层，不能因 revision
  // 改变而重新挂载 <video>、打断当前播放。画面/音频轨道变化才换 key 重载视频。
  const previewContentKey = useMemo(() => {
    return projectPreviewKey(project);
  }, [project]);
  const previewTimelineToken = useMemo(() => contentToken(previewContentKey), [previewContentKey]);
  const prepared = state.preparedPreview;
  const usePrepared = !!prepared && prepared.projectId === currentId &&
    prepared.contentKey === previewContentKey && sourceRestoreRevision === 0;
  const [settledSeek, setSettledSeek] = useState(playhead);
  useEffect(() => {
    if (playingRef.current && !state.scrubbing) return;
    if (!state.scrubbing || usePrepared) {
      setSettledSeek(playhead);
      return;
    }
    // Moving the ruler stays immediate; expensive segment loads wait briefly
    // for the pointer to settle. Pointer release flushes the exact final time.
    const timer = window.setTimeout(() => setSettledSeek(playhead), 80);
    return () => window.clearTimeout(timer);
  }, [playhead, state.scrubbing, usePrepared]);
  const decodePlayhead = state.scrubbing && !usePrepared ? settledSeek : playhead;
  const decodePlayheadRef = useRef(decodePlayhead);
  decodePlayheadRef.current = decodePlayhead;
  const windowIndex = Math.floor(Math.min(Math.max(0, decodePlayhead), Math.max(0, mediaEndSecs - 0.001)) / PREVIEW_WINDOW_SECONDS);
  const windowStart = usePrepared ? 0 : windowIndex * PREVIEW_WINDOW_SECONDS;
  const windowEnd = usePrepared ? mediaEndSecs : Math.min(mediaEndSecs, windowStart + PREVIEW_WINDOW_SECONDS);
  const hasVideoMedia = tracks.some(
    (t) => t.kind === "video" && t.visible !== false && (t.clips || []).some((c) => !c.hidden),
  );
  const hasAudioMedia = tracks.some(
    (t) => t.kind === "audio" && t.visible !== false && !t.muted && (t.clips || []).some((c) => !c.hidden),
  );
  const hasRenderable = hasVideoMedia || hasAudioMedia;
  const videoUrl =
    usePrepared ? prepared.url : !state.opening && currentId && state.revision && hasRenderable
      ? `/api/v1/projects/${encodeURIComponent(currentId)}/preview-window?index=${windowIndex}&timeline=${previewTimelineToken}&restore=${sourceRestoreRevision}`
      : "";
  const previewIdentity = `${currentId}:${previewTimelineToken}:${sourceRestoreRevision}`;
  const cacheScope = `${currentId}:${previewContentKey}:${sourceRestoreRevision}`;
  const segments = usePreviewSegments(cacheScope, videoUrl, mode === "video" && !usePrepared);
  const mediaUrl = usePrepared ? videoUrl : segments.media?.url || "";
  const videoReady = readyVideoUrl === videoUrl;
  const preparing = !!videoUrl && mode === "video" && !videoReady;
  const noticeToken = useMemo(() => ({}), [videoUrl, preparing, buffering]);
  const [loadingNotice, setLoadingNotice] = useState<object | null>(null);
  useEffect(() => {
    if (!preparing && !buffering) { setLoadingNotice(null); return; }
    const timer = window.setTimeout(() => setLoadingNotice(noticeToken), 200);
    return () => window.clearTimeout(timer);
  }, [preparing, buffering, noticeToken]);
  const showLoadingNotice = (preparing || buffering) && loadingNotice === noticeToken;
  const { canvasRef: handoffCanvasRef, bindVideo, confirmFrame, holding } = useVideoHandoff({
    videoRef, identity: previewIdentity,
    source: videoUrl, active: mode === "video", playing,
    onPresented: (source) => {
      setReadyVideoUrl(source);
      if (state.opening && usePrepared && source === prepared?.url && currentId &&
          (videoRef.current?.readyState || 0) >= 3) {
        dispatch({ type: "PROJECT_OPEN_READY", projectId: currentId });
      }
    },
  });
  const windowBoundsRef = useRef({ start: windowStart, end: windowEnd });
  windowBoundsRef.current = { start: windowStart, end: windowEnd };
  const bindPreviewVideo = useCallback((video: HTMLVideoElement | null) => {
    if (video && video.readyState >= 1) {
      const { start, end } = windowBoundsRef.current;
      const target = Math.min(end, Math.max(start, decodePlayheadRef.current));
      if (Math.abs(video.currentTime - (target - start)) > 0.03) video.currentTime = target - start;
      reportedVideoTimeRef.current = target;
    }
    bindVideo(video);
  }, [bindVideo]);
  const previewTime = mode === "video" && playing && videoReady ? videoTime : playhead;
  const hasVideoCoverage = hasVideoCoverageAt(tracks, previewTime);
  const selectedClip = state.selection?.clipId ? findClipById(state, state.selection.clipId) : null;
  const selectedTrackVisible = tracks.find((track) => track.id === state.selection?.trackId)?.visible !== false;
  const selectedHasColor = !!selectedClip?.effects?.some(
    (effect) => effect.enabled !== false && colorEffectIds.has(effect.effectId),
  );
  const colorCompareAvailable = !!selectedClip && !selectedClip.hidden &&
    selectedTrackVisible && selectedHasColor &&
    previewTime >= rationalSeconds(selectedClip.timelineStart) &&
    previewTime < rationalSeconds(selectedClip.timelineEnd);
  const posterUrl = usePreviewPoster({
    projectId: currentId, identity: videoUrl, time: decodePlayhead,
    enabled: preparing && hasVideoMedia && !holding && !state.opening,
  });
  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const measure = () => {
      const width = stage.clientWidth, height = stage.clientHeight;
      setStageSize((previous) => previous.width === width && previous.height === height
        ? previous : { width, height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    setReadyVideoUrl(null);
    setBuffering(false);
  }, [videoUrl]);
  const requestVideoPlay = useCallback((video: HTMLVideoElement) => {
    void video.play().catch((error: unknown) => {
      // Pause/source changes abort pending play; decode failures use the image fallback.
      if (video !== videoRef.current || !(error instanceof DOMException) || error.name !== "NotAllowedError") return;
      playingRef.current = false;
      setPlaying(false);
      dispatchRef.current({ type: "STATUS_SET", severity: "warn",
        text: "浏览器尚未开始播放，请再点击播放" });
    });
  }, []);
  const endPlay = useCallback(() => {
    playingRef.current = false;
    setPlaying(false);
    setBuffering(false);
    pauseStatusRef.current = "播放结束";
    dispatchRef.current({ type: "STATUS_SET", severity: "ok", text: "播放结束" });
  }, []);

  const { frameUrl, frameState, frameError, scheduleFrame, reset: resetFrames } = usePreviewFrame({
    projectId: currentId,
    active: mode === "image",
    playing: playing && !state.scrubbing,
    totalSecs: mediaEndSecs,
    playheadRef,
    dispatchRef,
    onEnded: endPlay,
  });
  useEffect(() => {
    if (mode === "image" && currentId) scheduleFrame(decodePlayhead);
  }, [mode, currentId, decodePlayhead, scheduleFrame]);
  useEffect(() => {
    if (!segments.error || mode !== "video") return;
    setMode("image");
    dispatchRef.current({ type: "STATUS_SET", severity: "warn",
      text: "连续预览失败，已切换到单帧预览" });
  }, [segments.error, mode]);

  useEffect(() => {
    const onAssetRestored = (event: Event) => {
      const path = (event as CustomEvent<{ path?: string }>).detail?.path;
      const isUsed = Boolean(path && project?.sequence.tracks.some((track) =>
        track.clips.some((clip) => clip.assetRef.sourcePath === path)));
      if (!isUsed) return;
      videoRef.current?.pause();
      setPlaying(false);
      setMode("video");
      resetFrames();
      // Force a new media element request after the original path becomes
      // available again; the project structure and its content token stay put.
      setSourceRestoreRevision((value) => value + 1);
      dispatchRef.current({ type: "PREPARED_PREVIEW_SET", preview: null });
      dispatchRef.current({ type: "STATUS_SET", severity: "ok",
        text: "素材已恢复，正在重新加载预览" });
    };
    window.addEventListener("cutvoke:asset-restored", onAssetRestored);
    return () => window.removeEventListener("cutvoke:asset-restored", onAssetRestored);
  }, [project, resetFrames]);

  useEffect(() => {
    let cancelled = false;
    void getEffectCatalog().then((specs) => {
      if (!cancelled) setColorEffectIds(new Set(specs.filter(
        (spec) => spec.browseCategory === "filter" || spec.browseCategory === "color",
      ).map((spec) => spec.effectId)));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const previous = colorBeforeUrlRef.current;
    if (previous) URL.revokeObjectURL(previous);
    colorBeforeUrlRef.current = null;
    setColorBeforeUrl(null);
    if (!showColorBefore || !colorCompareAvailable || !currentId || !selectedClip) {
      setColorBeforeState("idle");
      return;
    }
    let cancelled = false;
    setColorBeforeState("loading");
    void fetchPreviewFrame({
      projectId: currentId, t: previewTime, width: 960, height: 540,
      compareClipId: selectedClip.id,
    }).then((result) => {
      if (cancelled) {
        if (result.kind === "frame") URL.revokeObjectURL(result.url);
        return;
      }
      if (result.kind === "frame") {
        colorBeforeUrlRef.current = result.url;
        setColorBeforeUrl(result.url);
        setColorBeforeState("ok");
      } else {
        setColorBeforeState("error");
      }
    });
    return () => { cancelled = true; };
  }, [showColorBefore, colorCompareAvailable, currentId, selectedClip?.id,
      previewTime, state.revision]);

  useEffect(() => () => {
    if (colorBeforeUrlRef.current) URL.revokeObjectURL(colorBeforeUrlRef.current);
  }, []);

  // video 就绪 / 失败 → 切换主/降级模式
  const onVideoLoaded = useCallback((event: SyntheticEvent<HTMLVideoElement>) => {
    if (event.currentTarget !== videoRef.current) return;
    setMode("video");
    const v = videoRef.current;
    if (!v || Number.isNaN(v.duration) || v.duration <= 0) return;
    const target = Math.min(windowEnd, Math.max(windowStart, decodePlayheadRef.current));
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
    if (v.readyState >= 2 && !v.seeking) confirmFrame(v);
    if (playingRef.current && !scrubbingRef.current && v.paused && target < mediaEndSecs - 0.01) requestVideoPlay(v);
  }, [mediaEndSecs, windowStart, windowEnd, requestVideoPlay, confirmFrame]);
  const onVideoError = useCallback((video: HTMLVideoElement) => {
    if (video !== videoRef.current || video.dataset.previewSource !== videoUrl ||
        !video.error || video.error.code === 1) return;
    if (state.opening && usePrepared) {
      window.dispatchEvent(new CustomEvent("cutvoke:prepared-preview-error", { detail: { projectId: currentId } }));
      return;
    }
    setMode("image");
    dispatchRef.current({ type: "STATUS_SET", severity: "warn",
      text: "连续预览失败，已切换到单帧预览" });
    scheduleFrame(playheadRef.current);
  }, [scheduleFrame, state.opening, usePrepared, currentId, videoUrl]);
  const onVideoEnded = useCallback((event: SyntheticEvent<HTMLVideoElement>) => {
    if (event.currentTarget !== videoRef.current || scrubbingRef.current) return;
    if (windowEnd < mediaEndSecs - 0.01) {
      setVideoTime(windowEnd);
      dispatchRef.current({ type: "PLAYHEAD_SET", t: windowEnd });
      return;
    }
    setVideoTime(mediaEndSecs);
    dispatchRef.current({ type: "PLAYHEAD_SET", t: mediaEndSecs });
    endPlay();
  }, [endPlay, mediaEndSecs, windowEnd]);

  const onTimeUpdate = useCallback((event: SyntheticEvent<HTMLVideoElement>) => {
    const v = event.currentTarget;
    if (v !== videoRef.current) return;
    if (scrubbingRef.current) return;
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
        reportedVideoTimeRef.current = lp.in;
        setVideoTime(lp.in);
        dispatchRef.current({ type: "PLAYHEAD_SET", t: lp.in });
        return;
      }
    }
    if (!usePrepared && !v.paused && mediaEndSecs > 0 && absoluteTime >= mediaEndSecs - 0.01) {
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
  }, [endPlay, mediaEndSecs, mode, windowStart, windowEnd, usePrepared]);
  const onPlay = useCallback((event: SyntheticEvent<HTMLVideoElement>) => {
    if (event.currentTarget !== videoRef.current) return;
    setShowColorBefore(false);
    pauseStatusRef.current = "已暂停";
    setPlaying(true);
    playingRef.current = true;
    if (event.currentTarget.readyState < 3) {
      setBuffering(true);
      dispatchRef.current({ type: "STATUS_SET", severity: "ok", text: "正在准备播放，请稍等…" });
    }
  }, []);
  const onPlaying = useCallback((event: SyntheticEvent<HTMLVideoElement>) => {
    if (event.currentTarget !== videoRef.current) return;
    setBuffering(false);
    confirmFrame(event.currentTarget);
    dispatchRef.current({ type: "STATUS_SET", severity: "ok", text: "播放中" });
  }, [confirmFrame]);
  const onPause = useCallback((event: SyntheticEvent<HTMLVideoElement>) => {
    if (event.currentTarget !== videoRef.current) return;
    if (scrubPausedVideoRef.current === event.currentTarget) {
      scrubPausedVideoRef.current = null;
      return;
    }
    if (scrubbingRef.current) return;
    if (videoRef.current?.ended && windowEnd < mediaEndSecs - 0.01) return;
    setPlaying(false);
    playingRef.current = false;
    dispatchRef.current({ type: "STATUS_SET", severity: "ok", text: pauseStatusRef.current });
    pauseStatusRef.current = "已暂停";
  }, [windowEnd, mediaEndSecs]);
  const onWaiting = useCallback((event: SyntheticEvent<HTMLVideoElement>) => {
    if (event.currentTarget !== videoRef.current) return;
    if (scrubbingRef.current) return;
    if (!playingRef.current || event.currentTarget.ended) { setBuffering(false); return; }
    setBuffering(true);
    dispatchRef.current({ type: "STATUS_SET", severity: "ok",
      text: event.currentTarget.readyState < 2 ? "正在准备播放，请稍等…" : "正在缓冲，请稍等…" });
  }, []);
  const onCanPlay = useCallback((event: SyntheticEvent<HTMLVideoElement>) => {
    if (event.currentTarget !== videoRef.current) return;
    setBuffering(false);
    if (event.currentTarget.readyState >= 2 && !event.currentTarget.seeking) confirmFrame(event.currentTarget);
  }, [confirmFrame]);

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
    const boundedPlayhead = Math.min(mediaEndSecs, Math.max(0, decodePlayhead));
    if (state.scrubbing && !usePrepared && decodePlayhead !== playhead) return;
    setVideoTime(boundedPlayhead);
    const externalSeek = Math.abs(boundedPlayhead - reportedVideoTimeRef.current) > 0.25;
    if ((!playingRef.current || externalSeek) &&
        boundedPlayhead >= windowStart && boundedPlayhead <= windowEnd) {
      try {
        if (Math.abs(v.currentTime - (boundedPlayhead - windowStart)) > 0.03) {
          v.currentTime = Math.max(0, boundedPlayhead - windowStart);
        }
        // An action may already have positioned the native video. Accept that
        // seek too, otherwise the playback guard rejects every subsequent tick.
        reportedVideoTimeRef.current = boundedPlayhead;
      } catch {
        /* ignore */
      }
    }
  }, [decodePlayhead, playhead, state.scrubbing, usePrepared, mediaEndSecs, mode, windowStart, windowEnd]);

  // Publish once per decoded frame, rather than repainting the editor at the
  // display's 60/144Hz rate while a 30fps video is still showing the same frame.
  useEffect(() => {
    if (!playing || mode !== "video" || state.scrubbing) return;
    const video = videoRef.current;
    if (!video) return;
    let frameId = 0;
    let stopped = false;
    let lastPublished = -Infinity;
    const hasFrameCallback = typeof video.requestVideoFrameCallback === "function";
    const schedule = () => {
      frameId = hasFrameCallback ? video.requestVideoFrameCallback(sync)
        : window.requestAnimationFrame(sync);
    };
    const sync = (now: number) => {
      if (stopped || video !== videoRef.current || video.paused) return;
      const current = Math.min(mediaEndSecs, Math.max(0, windowStart + video.currentTime));
      if (Math.abs(playheadRef.current - reportedVideoTimeRef.current) <= 0.25 &&
          (hasFrameCallback || now - lastPublished >= 1000 / 30)) {
        lastPublished = now;
        reportedVideoTimeRef.current = current;
        setVideoTime(current);
        if (Math.abs(current - playheadRef.current) > 1 / 240)
          dispatchRef.current({ type: "PLAYHEAD_SET", t: current });
      }
      schedule();
    };
    schedule();
    return () => {
      stopped = true;
      if (hasFrameCallback) video.cancelVideoFrameCallback(frameId);
      else window.cancelAnimationFrame(frameId);
    };
  }, [playing, mode, mediaEndSecs, windowStart, state.scrubbing, videoUrl]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || mode !== "video") return;
    if (state.scrubbing) {
      if (!video.paused) {
        scrubPausedVideoRef.current = video;
        video.pause();
      }
    } else if (playingRef.current && video.paused) requestVideoPlay(video);
  }, [state.scrubbing, videoUrl, mode, requestVideoPlay]);

  // Retain the complete compressed next segment, ready for the next decoder.
  useEffect(() => {
    if (usePrepared || mode !== "video" || !videoReady || state.scrubbing || !playing ||
        !currentId || !hasRenderable || windowEnd >= mediaEndSecs - 0.01) return;
    const nextUrl = `/api/v1/projects/${encodeURIComponent(currentId)}/preview-window?index=${windowIndex + 1}&timeline=${previewTimelineToken}&restore=${sourceRestoreRevision}`;
    const lease = segments.cache.acquire(nextUrl);
    void lease.promise.catch(() => {});
    return lease.release;
  }, [playing, currentId, hasRenderable, windowEnd, mediaEndSecs, windowIndex,
    previewTimelineToken, sourceRestoreRevision, segments.cache, usePrepared, mode, videoReady, state.scrubbing]);

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
    setShowColorBefore(false);
    resetFrames();
    setPlaying(false);
    playingRef.current = false;
    setVideoTime(0);
    setSourceRestoreRevision(0);
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.currentTime = 0;
    }
  }, [currentId, resetFrames]);

  // 基础画面变化后重新尝试 video 模式；字幕更新不应让已降级的预览反复重试。
  useEffect(() => {
    setMode("video");
  }, [currentId, previewContentKey, sourceRestoreRevision]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const handleError = () => onVideoError(video);
    video.addEventListener("error", handleError);
    // A fast 4xx can fail the media request before React attaches its JSX event
    // handler. Check after the mode reset so an early error is not overwritten.
    if (video.error) handleError();
    return () => video.removeEventListener("error", handleError);
  }, [onVideoError, videoUrl, mode]);

  const togglePlay = () => {
    if (!currentId || !project) return;
    if (!hasRenderable) {
      dispatch({
        type: "STATUS_SET",
        severity: "warn",
        text: "请先将视频、图片或音频素材放入可见轨道",
      });
      return;
    }
    const v = videoRef.current;
    if (mode === "video" && v) {
      if (v.paused) {
        if (mediaEndSecs <= 0) return;
        if (playhead >= mediaEndSecs - 0.01 || windowStart + v.currentTime >= mediaEndSecs - 0.01) {
          if (windowStart === 0) v.currentTime = 0;
          reportedVideoTimeRef.current = 0;
          setVideoTime(0);
          dispatch({ type: "PLAYHEAD_SET", t: 0 });
          if (windowStart !== 0) {
            setPlaying(true);
            return;
          }
        }
        playingRef.current = true;
        setPlaying(true);
        requestVideoPlay(v);
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
    playingRef.current = false;
    setPlaying(false);
    reportedVideoTimeRef.current = 0;
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
    playingRef.current = false;
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

  const fittedWidth = Math.min(Math.max(1, stageSize.width - 16),
    Math.max(1, stageSize.height - 16) * ratio[0] / ratio[1]);
  const frameStyle: React.CSSProperties =
    zoom === FIT
      ? { width: fittedWidth, height: fittedWidth * ratio[1] / ratio[0] }
      : { width: `${640 * zoom}px`, height: `${360 * zoom}px`, aspectRatio: "auto" };

  const shownTime = mode === "video" ? videoTime : playhead;
  const totalDisplay = mediaEndSecs;
  const ratioLabel = PLAYER_RATIOS.find((x) => x.wh[0] === ratio[0] && x.wh[1] === ratio[1])?.label || "16:9";
  const canvasWidth = project?.sequence.width || 1920;
  const canvasHeight = project?.sequence.height || 1080;
  const canvasBackground = project?.sequence.backgroundColor || "#000000";
  const backgroundSwatches = [
    { name: "黑色", color: "#000000" },
    { name: "白色", color: "#FFFFFF" },
    { name: "深蓝灰", color: "#29364A" },
    { name: "暖米白", color: "#EFE7D8" },
    { name: "浅粉", color: "#F1DDE3" },
  ];

  const saveCanvasBackground = async () => {
    if (backgroundSaving || !currentId || state.editLock) return;
    const latest = getLatestState() || state;
    setBackgroundSaving(true);
    try {
      const result = await runCommand(dispatch, latest, "sequence.background", {
        color: backgroundDraft.toUpperCase(),
      });
      if (result.ok) setBackgroundPickerOpen(false);
    } finally {
      setBackgroundSaving(false);
    }
  };

  return (
    <section className="player">
      <div className="player__stage" ref={stageRef}>
        <div className="player__canvas-frame" style={{ ...frameStyle, backgroundColor: canvasBackground }} aria-label="预览画面" aria-busy={preparing || buffering}>
          <canvas ref={handoffCanvasRef} className="player__continuity-frame" aria-hidden="true" />
          {!videoUrl ? (
            <div className="player__placeholder">暂无可播放片段：导入素材并拖到时间线后此处显示预览</div>
          ) : mode === "video" ? (
            <>
              <PreviewVideoPool key={cacheScope} source={videoUrl} src={mediaUrl}
                bytes={usePrepared ? 0 : segments.media?.bytes || 0} identity={previewIdentity}
                visible={hasVideoCoverage} bindVideo={bindPreviewVideo}
                events={{ onLoadedMetadata: onVideoLoaded, onLoadedData: onVideoLoaded,
                  onTimeUpdate, onPlay, onPlaying, onPause, onEnded: onVideoEnded,
                  onWaiting, onCanPlay, onSeeked: onVideoLoaded,
                  onError: event => onVideoError(event.currentTarget) }} />
              {preparing && posterUrl ? (
                <img className="player__poster" src={posterUrl} alt="预览首帧" draggable={false} />
              ) : null}
              {!hasVideoMedia ? <div className="player__placeholder">仅音频工程 · 画面为空</div> : null}
              {showLoadingNotice ? (
                <div className="player__loading-notice" role="status" aria-live="polite" aria-atomic="true">
                  <LoaderCircle className="player__loading-spinner" size={18} aria-hidden="true" />
                  <div className="player__loading-copy">
                    <strong>{preparing ? "正在准备预览…" : "播放缓冲中…"}</strong>
                    <span>{preparing
                      ? `${holding ? "已保留当前画面，正在加载目标位置。" : posterUrl ? "首帧已显示，正在准备连续预览。" : "正在加载当前位置的画面，请稍等。"}${playing ? "准备完成后自动播放。" : "准备完成后即可播放。"}`
                      : "正在加载后续画面，请稍等。"}</span>
                  </div>
                  {preparing && playing ? (
                    <button type="button" className="tool-btn player__loading-cancel" aria-label="取消等待播放"
                      onClick={() => {
                        playingRef.current = false;
                        setPlaying(false);
                        videoRef.current?.pause();
                        dispatchRef.current({ type: "STATUS_SET", severity: "ok", text: "已取消自动播放" });
                      }}>取消</button>
                  ) : null}
                </div>
              ) : null}
            </>
          ) : frameUrl && frameState !== "empty" ? (
            <>
              <img src={frameUrl} alt="时间线预览帧" draggable={false} />
              {frameState === "error" ? (
                <div className="player__loading-notice" role="alert">
                  <span>{formatPreviewError(frameError)}</span>
                  <button type="button" className="tool-btn" onClick={() => {
                    resetFrames(); scheduleFrame(playheadRef.current);
                  }}>重试预览</button>
                </div>
              ) : null}
            </>
          ) : frameState === "empty" ? (
            <div className="player__placeholder">该时刻无片段（黑帧）</div>
          ) : frameState === "loading" ? (
            <div className="cv-loading">加载预览…</div>
          ) : (
            <div className="player__preview-error" role="alert">
              <strong>预览不可用</strong>
              <span>{formatPreviewError(frameError)}</span>
              <button type="button" className="tool-btn"
                onClick={() => {
                  resetFrames();
                  scheduleFrame(playheadRef.current);
                }}>
                重试预览
              </button>
            </div>
          )}
          {videoUrl && mode !== "video" ? <span className="player__mode-badge">预览降级：图片</span> : null}
          {showColorBefore && colorBeforeState === "ok" && colorBeforeUrl ? (
            <>
              <img className="player__compare-frame" src={colorBeforeUrl}
                alt="所选片段调色前画面" draggable={false} />
              <span className="player__compare-label">调色前</span>
            </>
          ) : null}
          {showColorBefore && colorBeforeState === "loading" ? (
            <span className="player__compare-label">正在生成调色前画面…</span>
          ) : null}
          <TransformCanvasOverlay />
          <MaskCanvasOverlay />
          <StickerCanvasOverlay />
          <CropCanvasOverlay />
          <CaptionCanvasOverlay />
          {showTitleSafeArea && currentId ? (
            <svg className="player__title-safe-area" viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
              preserveAspectRatio="xMidYMid meet" role="img"
              aria-label="标题安全区四边各留百分之十，动作安全区四边各留百分之五">
              <rect className="player__safe-guide player__safe-guide--title" data-guide="title-safe"
                x={canvasWidth * 0.1} y={canvasHeight * 0.1}
                width={canvasWidth * 0.8} height={canvasHeight * 0.8} />
              <rect className="player__safe-guide player__safe-guide--action" data-guide="action-safe"
                x={canvasWidth * 0.05} y={canvasHeight * 0.05}
                width={canvasWidth * 0.9} height={canvasHeight * 0.9} />
            </svg>
          ) : null}
        </div>
      </div>

      {backgroundPickerOpen && currentId ? (
        <div className="player__background-popover" role="dialog" aria-label="画布背景设置">
          <div className="player__background-heading">画布背景</div>
          <div className="player__background-swatches" aria-label="预设底色">
            {backgroundSwatches.map((swatch) => (
              <button key={swatch.color} type="button"
                className={`player__background-swatch${backgroundDraft.toUpperCase() === swatch.color ? " player__background-swatch--selected" : ""}`}
                style={{ backgroundColor: swatch.color }}
                aria-label={`${swatch.name} ${swatch.color}`}
                aria-pressed={backgroundDraft.toUpperCase() === swatch.color}
                onClick={() => setBackgroundDraft(swatch.color)} />
            ))}
          </div>
          <label className="player__background-custom">
            自定义颜色
            <input type="color" aria-label="自定义画布背景颜色" value={backgroundDraft}
              onChange={(event) => setBackgroundDraft(event.currentTarget.value.toUpperCase())} />
          </label>
          <div className="player__background-actions">
            <button type="button" className="tool-btn" onClick={() => setBackgroundPickerOpen(false)}>
              取消
            </button>
            <button type="button" className="tool-btn tool-btn--primary"
              onClick={() => void saveCanvasBackground()}
              disabled={backgroundSaving || !!state.editLock}>
              {backgroundSaving ? "保存中…" : "应用"}
            </button>
          </div>
        </div>
      ) : null}

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
        <button type="button"
          className={`player__safe-toggle ${showColorBefore ? "player__safe-toggle--on" : ""}`}
          onClick={() => {
            videoRef.current?.pause();
            setPlaying(false);
            setShowColorBefore((value) => !value);
          }}
          disabled={!currentId || !colorCompareAvailable}
          aria-label="切换调色前后对比" aria-pressed={showColorBefore}
          title={colorCompareAvailable ? "显示所选片段在当前时间点的调色前画面；再次点击返回成片画面"
            : "先选中有滤镜或调色效果的片段，并把播放头移到该片段内"}>
          {showColorBefore ? "调色前" : "前后对比"}
        </button>
        {showColorBefore && colorBeforeState === "error" ? (
          <span className="player__compare-error" role="alert">调色前预览失败</span>
        ) : null}
        <button className="player__iconbtn player-controls__view" onClick={cycleRatio} title="画布比例（仅预览显示）" aria-label="切换画布比例">
          <RatioIcon r={ratio} />
          <span className="player__zoom-val player__ratio-val">{ratioLabel}</span>
        </button>
        <button type="button" className={`player__safe-toggle${backgroundPickerOpen ? " player__safe-toggle--on" : ""}`}
          onClick={() => {
            setBackgroundDraft(canvasBackground.toUpperCase());
            setBackgroundPickerOpen((open) => !open);
          }}
          disabled={!currentId || !!state.editLock}
          aria-label="设置画布背景" aria-expanded={backgroundPickerOpen}
          title="设置透出区域与空白区域的画布底色">
          <span className="player__background-chip" style={{ backgroundColor: canvasBackground }} />背景
        </button>
        <button type="button" className={`player__safe-toggle ${showTitleSafeArea ? "player__safe-toggle--on" : ""}`}
          onClick={() => setShowTitleSafeArea((value) => !value)} disabled={!currentId}
          aria-label="切换标题安全区" aria-pressed={showTitleSafeArea}
          title="显示或隐藏标题安全区（10%）与动作安全区（5%）；仅用于预览参考">安全区</button>
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
