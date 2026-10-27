import { msg, tr, useLocale } from "../i18n";
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Film, LoaderCircle, TriangleAlert } from "lucide-react";
import { useEditor } from "../store/editor";
import { getLatestState, refreshProject } from "../store/actions";
import { ApiFailure } from "../lib/api";
import { previewContentKey, previewRequest, type PreviewJob } from "../lib/previewPreparation";
import { fetchPreviewFrame } from "../lib/mediaApi";
import { usePreviewBudget } from "../lib/previewBudget";

const pause = (signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  const abort = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
  const timer = window.setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 250);
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) abort();
});

export function useProjectPreparation() {
  const budget = usePreviewBudget();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [retry, setRetry] = useState(0);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState("reading");
  const [job, setJob] = useState<PreviewJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const contentKey = useMemo(() => previewContentKey(state.project), [state.project]);
  const projectRef = useRef(state.project);
  projectRef.current = state.project;
  const openingRef = useRef(state.opening);
  openingRef.current = state.opening;
  const decodeTimerRef = useRef(0);
  const cancelPreparationRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (!state.opening) clearTimeout(decodeTimerRef.current);
  }, [state.opening]);

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
    if (!state.currentId || !state.project || state.view !== "editor") return;
    const project = projectRef.current!;
    const projectId = state.currentId;
    const initialOpening = state.opening;
    const controller = new AbortController();
    const { signal } = controller;
    let jobId: string | undefined;
    let budgetTimer = 0;
    let enteredEditor = !initialOpening;
    const cancelPreparation = () => {
      controller.abort();
      clearTimeout(decodeTimerRef.current);
      clearTimeout(budgetTimer);
      if (jobId) void previewRequest(`preview-jobs/${jobId}/cancel`, undefined, {}).catch(() => {});
    };
    setError(null); setJob(null); setProgress(0); setPhase("checking");
    dispatch({ type: "PREPARED_PREVIEW_SET", preview: null });
    const renderable = project.sequence.tracks.some((track) => track.visible !== false &&
      (track.kind === "video" || track.kind === "text" || (track.kind === "audio" && !track.muted)) &&
      track.clips.some((clip) => !clip.hidden));
    if (!renderable) {
      dispatch({ type: "PROJECT_OPEN_READY", projectId });
      return;
    }
    cancelPreparationRef.current = cancelPreparation;
    const failedDecode = (event: Event) => {
      if ((event as CustomEvent).detail?.projectId === projectId) {
        clearTimeout(decodeTimerRef.current);
        setError(tr("连续预览无法解码，请重试加载。"));
      }
    };
    window.addEventListener("cutvoke:prepared-preview-error", failedDecode);
    // Editing needs the current composed frame, not several seconds of video.
    // Request it before queuing background windows so it gets first admission.
    if (initialOpening) void fetchPreviewFrame({ projectId, t: 0, width: 640, height: 360, signal }).then(async (result) => {
      if (result.kind === "error" || result.kind === "cancelled") return;
      if (result.kind === "frame") {
        try {
          const image = new Image();
          image.src = result.url;
          await image.decode();
        } finally { URL.revokeObjectURL(result.url); }
      }
      if (signal.aborted) return;
      enteredEditor = true;
      dispatch({ type: "PROJECT_OPEN_READY", projectId });
    }).catch(() => { /* The window preparation below reports actionable errors. */ });
    void (async () => {
      if (!budget.enabled || !budget.continuous || budget.range?.projectId === projectId) {
        if (initialOpening) dispatch({ type: "PROJECT_OPEN_READY", projectId });
        return;
      }
      // A visual edit invalidates continuous playback too. Rebuild after a short
      // idle interval instead of leaving edited complex projects on short
      // windows forever; subsequent edits cancel this immutable request.
      if (!initialOpening) for (let tick = 0; tick < Math.ceil(budget.delayMs / 250); tick++) await pause(signal);
      if (signal.aborted) return;
      let current = await previewRequest<PreviewJob>(
        `projects/${encodeURIComponent(projectId)}/preview-jobs`, undefined, { revision: project.revision, editorTitles: true });
      jobId = current.jobId;
      budgetTimer = window.setTimeout(() => {
        if (initialOpening) dispatch({ type: "PROJECT_OPEN_READY", projectId });
        cancelPreparation();
      }, budget.wallSeconds * 1000);
      while (true) {
        if (signal.aborted) return;
        setJob(current); setPhase(current.phase); setProgress(Math.floor(current.progress * 0.9));
        if (current.state === "failed" || current.state === "cancelled") {
          throw new Error(current.error || tr("预览准备已取消，请重试。"));
        }
        if (current.state === "completed") break;
        if (!enteredEditor && current.completedWindows > 0) {
          enteredEditor = true;
          dispatch({ type: "PROJECT_OPEN_READY", projectId });
        }
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
      if (openingRef.current) decodeTimerRef.current = window.setTimeout(
        () => setError(tr("首帧解码超时，请重试加载。")), 15000);
    })().catch(async (failure: unknown) => {
      if (!signal.aborted && failure instanceof ApiFailure && failure.code === "REVISION_CONFLICT") {
        // A local edit can commit before its new snapshot reaches this worker.
        // Reconcile that immutable request without replacing the edit feedback
        // with a false reload warning (or a generic ready message).
        const refreshed = await refreshProject(dispatch, projectId, { quiet: true });
        if (signal.aborted) return;
        const latest = getLatestState();
        if (refreshed && latest?.currentId === projectId && latest.project?.revision !== project.revision) {
          setRetry(value => value + 1);
          return;
        }
      }
      if (!signal.aborted && !jobId && failure instanceof ApiFailure && failure.status === 404) {
        dispatch({ type: "PROJECT_OPEN_READY", projectId });
        dispatch({ type: "STATUS_SET", severity: "warn",
          text: msg("重启 CutVoke 服务后可启用完整预览加载，当前使用分段预览") });
        return;
      }
      if (!signal.aborted) {
        const message = failure instanceof Error ? failure.message : tr("预览准备失败");
        if (openingRef.current) setError(message);
        else dispatch({ type: "STATUS_SET", severity: "warn", text: message });
      }
    }).finally(() => {
      clearTimeout(budgetTimer);
      // Let submission return its ID even after navigation so cancellation owns the right job.
      if (signal.aborted && jobId) void previewRequest(`preview-jobs/${jobId}/cancel`, undefined, {}).catch(() => {});
    });
    return () => {
      cancelPreparation();
      if (cancelPreparationRef.current === cancelPreparation) cancelPreparationRef.current = null;
      window.removeEventListener("cutvoke:prepared-preview-error", failedDecode);
    };
    // Opening ends when the first frame/window is ready. Keep this worker/poll alive
    // until the continuous preview completes; navigation or a visual edit owns
    // cancellation. An opening-state change alone must not cancel the job.
  }, [state.currentId, state.view, contentKey, retry, dispatch, budget]);

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
      cancelPreparationRef.current?.();
      dispatch({ type: "PREPARED_PREVIEW_SET", preview: null });
      if (state.currentId) dispatch({ type: "PROJECT_OPEN_READY", projectId: state.currentId });
    },
  };
}

export function ProjectLoading({ preparation }: { preparation: ReturnType<typeof useProjectPreparation> }) {
  useLocale();
  const { state } = useEditor({ subscribeToClock: false });
  const { progress, phase, job, error } = preparation;
  const missingSource = error?.match(/source file missing for clip .+?:\s*(.+)$/i);
  const readableError = missingSource
    ? tr("素材文件不存在：{0}。恢复文件后可重试，或进入工程重新链接素材。", missingSource[1]) : error;
  const titles: Record<string, string> = {
    reading: tr("正在读取工程"), checking: tr("正在检查预览缓存"), queued: tr("等待预览准备"),
    rendering: tr("正在准备首段预览"), assembling: tr("正在合成连续预览"),
    verifying: tr("正在检查预览结果"), ready: tr("预览准备完成"),
    loading: tr("正在加载连续预览"), decoding: tr("正在显示首帧"),
  };
  const stage = ["reading", "checking", "queued"].includes(phase) ? 0
    : ["loading", "decoding", "ready"].includes(phase) ? 2 : 1;
  return (
    <section className="project-loading" role="dialog" aria-modal="true" aria-labelledby="project-loading-title">
      <div className="project-loading__card">
        <div className={`project-loading__icon${error ? " project-loading__icon--error" : ""}`}>
          {error ? <TriangleAlert size={32} /> : <Film size={32} />}
        </div>
        <span className="project-loading__eyebrow">{tr("CUTVOKE · 工程准备")}</span>
        <h1 id="project-loading-title">{error ? tr("工程预览准备失败") : titles[phase] || tr("正在准备工程")}</h1>
        <p className="project-loading__name">{state.projectName || state.currentId}</p>
        {error ? <p className="project-loading__error" role="alert">{readableError}</p> : (
          <>
            <div className="project-loading__progress" role="progressbar" aria-label={tr("工程加载进度")}
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
              <div style={{ width: `${progress}%` }} />
            </div>
            {job?.phase === "rendering" && job.currentWindow ? <p className="project-loading__hint" role="status">
              {tr("正在渲染第 {0} 段（{1}–{2} 秒），已用 {3} 秒", job.currentWindow, job.windowStart, job.windowEnd, job.elapsedSeconds)}
            </p> : null}
            <div className="project-loading__detail" role="status" aria-live="polite">
              <span>{job?.cached ? tr("正在复用已准备的预览") : job && job.totalWindows > 0
                ? tr("已准备 {0} / {1} 段", job.completedWindows, job.totalWindows) : tr("正在读取时间线与素材")}</span>
              <span className="cv-mono">{progress}%</span>
            </div>
            <ol className="project-loading__steps">
              {[tr("读取工程"), tr("准备完整预览"), tr("加载画面")].map((label, index) => (
                <li key={label} data-active={index === stage} data-done={index < stage}>
                  {index < stage ? <Check size={15} /> : index === stage ? <LoaderCircle size={15} /> : <span />}
                  {label}
                </li>
              ))}
            </ol>
            <p className="project-loading__hint">{tr("首帧显示后即可进入编辑，连续预览会在后台继续准备。")}</p>
          </>
        )}
        <div className="project-loading__actions">
          <button className="tool-btn" autoFocus onClick={preparation.back}>{tr("返回工程列表")}</button>
          {!error && state.project ? <button className="tool-btn" onClick={preparation.repair}>{tr("先进入编辑，按需预览")}</button> : null}
          {error && state.project ? <button className="tool-btn" onClick={preparation.repair}>{tr("进入工程修复")}</button> : null}
          {error ? <button className="tool-btn tool-btn--primary" onClick={preparation.retry}>{tr("重试加载")}</button> : null}
        </div>
      </div>
    </section>
  );
}
