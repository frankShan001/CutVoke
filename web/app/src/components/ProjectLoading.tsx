import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Film, LoaderCircle, TriangleAlert } from "lucide-react";
import { useEditor } from "../store/editor";
import { refreshProject } from "../store/actions";
import { ApiFailure } from "../lib/api";
import { previewContentKey, previewRequest, type PreviewJob } from "../lib/previewPreparation";

const pause = (signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  const abort = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
  const timer = window.setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 250);
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) abort();
});

export function useProjectPreparation() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [retry, setRetry] = useState(0);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState("reading");
  const [job, setJob] = useState<PreviewJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const contentKey = useMemo(() => previewContentKey(state.project), [state.project]);
  const projectRef = useRef(state.project);
  projectRef.current = state.project;

  useEffect(() => {
    const match = state.preparedPreview?.url.match(/\/preview-jobs\/([^/]+)\/media$/);
    if (!match) return;
    // Keep active local playback out of disk eviction, even while paused.
    const refresh = () => void previewRequest(`preview-jobs/${match[1]}/keepalive`, undefined, {}).catch(() => {});
    refresh();
    const timer = window.setInterval(refresh, 10000);
    return () => clearInterval(timer);
  }, [state.preparedPreview?.url]);

  useEffect(() => {
    if (state.opening) { setError(null); setProgress(0); setPhase("reading"); setJob(null); }
  }, [state.opening, state.currentId]);

  useEffect(() => {
    if (!state.opening || !state.currentId || !state.project) return;
    const project = projectRef.current!;
    const projectId = state.currentId;
    const controller = new AbortController();
    const { signal } = controller;
    let jobId: string | undefined;
    let decodeTimer = 0;
    setError(null); setJob(null); setProgress(0); setPhase("checking");
    dispatch({ type: "PREPARED_PREVIEW_SET", preview: null });
    const renderable = project.sequence.tracks.some((track) => track.visible !== false &&
      (track.kind === "video" || (track.kind === "audio" && !track.muted)) &&
      track.clips.some((clip) => !clip.hidden));
    if (!renderable) {
      dispatch({ type: "PROJECT_OPEN_READY", projectId });
      return;
    }
    const failedDecode = (event: Event) => {
      if ((event as CustomEvent).detail?.projectId === projectId) {
        clearTimeout(decodeTimer);
        setError("连续预览无法解码，请重试加载。");
      }
    };
    window.addEventListener("cutvoke:prepared-preview-error", failedDecode);
    void (async () => {
      let current = await previewRequest<PreviewJob>(
        `projects/${encodeURIComponent(projectId)}/preview-jobs`, undefined, { revision: project.revision });
      jobId = current.jobId;
      while (true) {
        if (signal.aborted) return;
        setJob(current); setPhase(current.phase); setProgress(Math.floor(current.progress * 0.9));
        if (current.state === "failed" || current.state === "cancelled") {
          throw new Error(current.error || "预览准备已取消，请重试。");
        }
        if (current.state === "completed") break;
        await pause(signal);
        current = await previewRequest<PreviewJob>(`preview-jobs/${jobId}`, signal);
      }
      setPhase("loading"); setProgress(90);
      // The complete composition is already on local disk. Stream its cached
      // bytes with native Range requests instead of duplicating long projects
      // into a whole-file Blob in renderer memory. No render occurs on seeks.
      const url = `/api/v1/preview-jobs/${jobId}/media`;
      setPhase("decoding"); setProgress(98);
      dispatch({ type: "PREPARED_PREVIEW_SET", preview: { projectId, contentKey, url } });
      decodeTimer = window.setTimeout(() => setError("首帧解码超时，请重试加载。"), 15000);
    })().catch((failure: unknown) => {
      if (!signal.aborted && !jobId && failure instanceof ApiFailure && failure.status === 404) {
        dispatch({ type: "PROJECT_OPEN_READY", projectId });
        dispatch({ type: "STATUS_SET", severity: "warn",
          text: "重启 CutVoke 服务后可启用完整预览加载，当前使用分段预览" });
        return;
      }
      if (!signal.aborted) setError(failure instanceof Error ? failure.message : "预览准备失败");
    }).finally(() => {
      // Let submission return its ID even after navigation so cancellation owns the right job.
      if (signal.aborted && jobId) void previewRequest(`preview-jobs/${jobId}/cancel`, undefined, {}).catch(() => {});
    });
    return () => {
      controller.abort(); clearTimeout(decodeTimer);
      window.removeEventListener("cutvoke:prepared-preview-error", failedDecode);
      if (jobId) void previewRequest(`preview-jobs/${jobId}/cancel`, undefined, {}).catch(() => {});
    };
  }, [state.opening, state.currentId, contentKey, retry, dispatch]);

  return {
    progress: state.project ? progress : 0,
    phase: state.project ? phase : "reading",
    job, error: error || (!state.project ? state.error?.message : null),
    retry: () => {
      dispatch({ type: "ERROR_SET", error: null });
      setError(null); setRetry((value) => value + 1);
      if (state.currentId) void refreshProject(dispatch, state.currentId);
    },
    back: () => dispatch({ type: "PROJECT_CLOSED" }),
    repair: () => {
      dispatch({ type: "PREPARED_PREVIEW_SET", preview: null });
      if (state.currentId) dispatch({ type: "PROJECT_OPEN_READY", projectId: state.currentId });
    },
  };
}

export function ProjectLoading({ preparation }: { preparation: ReturnType<typeof useProjectPreparation> }) {
  const { state } = useEditor({ subscribeToClock: false });
  const { progress, phase, job, error } = preparation;
  const missingSource = error?.match(/source file missing for clip .+?:\s*(.+)$/i);
  const readableError = missingSource
    ? `素材文件不存在：${missingSource[1]}。恢复文件后可重试，或进入工程重新链接素材。` : error;
  const titles: Record<string, string> = {
    reading: "正在读取工程", checking: "正在检查预览缓存", queued: "等待预览准备",
    rendering: "正在准备完整预览", assembling: "正在合成连续预览",
    verifying: "正在检查预览结果", ready: "预览准备完成",
    loading: "正在加载连续预览", decoding: "正在显示首帧",
  };
  const stage = ["reading", "checking", "queued"].includes(phase) ? 0
    : ["loading", "decoding", "ready"].includes(phase) ? 2 : 1;
  return (
    <section className="project-loading" role="dialog" aria-modal="true" aria-labelledby="project-loading-title">
      <div className="project-loading__card">
        <div className={`project-loading__icon${error ? " project-loading__icon--error" : ""}`}>
          {error ? <TriangleAlert size={32} /> : <Film size={32} />}
        </div>
        <span className="project-loading__eyebrow">CUTVOKE · 工程准备</span>
        <h1 id="project-loading-title">{error ? "工程预览准备失败" : titles[phase] || "正在准备工程"}</h1>
        <p className="project-loading__name">{state.projectName || state.currentId}</p>
        {error ? <p className="project-loading__error" role="alert">{readableError}</p> : (
          <>
            <div className="project-loading__progress" role="progressbar" aria-label="工程加载进度"
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
              <div style={{ width: `${progress}%` }} />
            </div>
            <div className="project-loading__detail" role="status" aria-live="polite">
              <span>{job?.cached ? "正在复用已准备的预览" : job && job.totalWindows > 0
                ? `已准备 ${job.completedWindows} / ${job.totalWindows} 段` : "正在读取时间线与素材"}</span>
              <span className="cv-mono">{progress}%</span>
            </div>
            <ol className="project-loading__steps">
              {["读取工程", "准备完整预览", "加载画面"].map((label, index) => (
                <li key={label} data-active={index === stage} data-done={index < stage}>
                  {index < stage ? <Check size={15} /> : index === stage ? <LoaderCircle size={15} /> : <span />}
                  {label}
                </li>
              ))}
            </ol>
            <p className="project-loading__hint">首次打开需要生成完整预览，复杂工程准备时间会更长。完成后可直接播放，再次打开会复用缓存。</p>
          </>
        )}
        <div className="project-loading__actions">
          <button className="tool-btn" autoFocus onClick={preparation.back}>返回工程列表</button>
          {error && state.project ? <button className="tool-btn" onClick={preparation.repair}>进入工程修复</button> : null}
          {error ? <button className="tool-btn tool-btn--primary" onClick={preparation.retry}>重试加载</button> : null}
        </div>
      </div>
    </section>
  );
}
