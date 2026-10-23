import { msg, tr, useLocale } from "../i18n";
/** Export requests and measured stage progress are restored from persisted jobs. */

import { useEffect, useRef, useState } from "react";
import { Upload, Copy, FolderOpen, Loader2, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Button, Field, Select, TextInput, Badge } from "./ui";
import { useEditor, showError } from "../store/editor";
import { doExport, getLatestState } from "../store/actions";
import { cancelExportJob, listExportJobs, preflightProject, ApiFailure, type ExportQuality, type ExportJob, type ProjectPreflightIssue, type ProjectPreflightReport } from "../lib/api";
import { assetDisplayName, useSessionAssets } from "../lib/assetStore";
import { LOCATE_PREFLIGHT_ISSUE, preflightSummary } from "../lib/preflight";
import { rationalToSecs } from "../lib/rational";
import type { ExportResult } from "../types/api";

function savedExportSettings(projectId: string | null) {
  try { return JSON.parse(localStorage.getItem(`cutvoke.export.${projectId}`) || "null") as {
    outPath: string; quality: ExportQuality; useRange: boolean; rangeStart: string; rangeEnd: string;
    videoBitrate: string; audioBitrate: string; transparent: boolean;
  } | null; } catch { return null; }
}

function clockTime(seconds: number) {
  const rounded = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
}

export function ExportVideoSection({ onClose }: { onClose: () => void }) {
  useLocale();
  const { state, dispatch } = useEditor();
  useSessionAssets();
  const [draft] = useState(() => savedExportSettings(state.currentId));
  const [outPath, setOutPath] = useState(draft?.outPath || "");
  const [quality, setQuality] = useState<ExportQuality>(draft?.quality || "high");
  const [useRange, setUseRange] = useState(draft?.useRange || false);
  const [rangeStart, setRangeStart] = useState(draft?.rangeStart || "0");
  const [rangeEnd, setRangeEnd] = useState(draft?.rangeEnd || "");
  const [videoBitrate, setVideoBitrate] = useState(draft?.videoBitrate || "");
  const [audioBitrate, setAudioBitrate] = useState(draft?.audioBitrate || "");
  const [transparent, setTransparent] = useState(draft?.transparent || false);
  const [job, setJob] = useState<ExportJob | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [result, setResult] = useState<ExportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<"idle" | "checking" | "blocked" | "running" | "done" | "error" | "cancelled">("idle");
  const [preflight, setPreflight] = useState<ProjectPreflightReport | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [jobError, setJobError] = useState<ApiFailure | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [reconnecting, setReconnecting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const startedRef = useRef(Date.now());
  const projectDuration = (state.project?.sequence.tracks || []).reduce(
    (duration, track) => Math.max(
      duration,
      ...track.clips.map((clip) => rationalToSecs(clip.timelineEnd)),
    ),
    0,
  );
  const frameRate = state.project?.sequence.fps
    ? rationalToSecs(state.project.sequence.fps)
    : 0;
  const frameDuration = frameRate > 0 ? 1 / frameRate : 0.01;
  const snapToFrame = (value: string) => {
    if (!value.trim()) return value;
    const seconds = Number(value);
    if (!Number.isFinite(seconds)) return value;
    return String(Number((Math.round(seconds / frameDuration) * frameDuration).toFixed(6)));
  };
  const aliveRef = useRef(true);
  useEffect(() => {
    if (!state.currentId) return;
    try { localStorage.setItem(`cutvoke.export.${state.currentId}`, JSON.stringify({
      outPath, quality, useRange, rangeStart, rangeEnd, videoBitrate, audioBitrate, transparent,
    })); } catch { /* Export remains available when browser storage is denied. */ }
  }, [state.currentId, outPath, quality, useRange, rangeStart, rangeEnd, videoBitrate, audioBitrate, transparent]);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setElapsed(Math.max(0, Math.floor((Date.now() - startedRef.current) / 1000))), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  // Persisted jobs survive closing the panel. Slow rendering is not a failure.
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const restore = async () => {
      try {
        const jobs = state.currentId ? await listExportJobs(state.currentId) : [];
        if (stopped) return;
        const job = jobs.find(item => ["queued", "running"].includes(item.status)) || jobs[0];
        if (job) {
          setJob(job);
          if (!draft || ["queued", "running"].includes(job.status)) {
            setOutPath(job.outPath);
            if (["high", "medium", "low"].includes(job.quality)) setQuality(job.quality as ExportQuality);
            setVideoBitrate(job.request?.videoBitrateKbps?.toString() || "");
            setAudioBitrate(job.request?.audioBitrateKbps?.toString() || "");
            setTransparent(Boolean(job.request?.transparent));
            setUseRange(Boolean(job.request?.range));
            setRangeStart(job.request?.range?.start.toString() || "0");
            setRangeEnd(job.request?.range?.end.toString() || "");
          }
          const created = Number(job.createdAt);
          startedRef.current = Number.isFinite(created) && created > 0 ? created * 1000 : Date.parse(String(job.createdAt)) || Date.now();
          if (["queued", "running"].includes(job.status)) {
            setBusy(true);
            setPhase("running");
            setElapsed(Math.max(0, Math.floor((Date.now() - startedRef.current) / 1000)));
          }
          setJobId(job.jobId);
        }
        setReconnecting(false);
        setRestoring(false);
      } catch {
        if (!stopped) { setReconnecting(true); timer = setTimeout(restore, 1500); }
      }
    };
    void restore();
    return () => { stopped = true; clearTimeout(timer); };
  }, [state.currentId]);

  useEffect(() => {
    if (!jobId || !state.currentId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const projectId = state.currentId;
    const poll = async () => {
      let job: ExportJob | undefined;
      try {
        job = (await listExportJobs(projectId)).find(item => item.jobId === jobId);
        if (stopped) return;
        setReconnecting(!job);
      } catch {
        if (stopped) return;
        setReconnecting(true);
      }
      if (!job) { timer = setTimeout(poll, 1500); return; }
      setJob(job);
      if (job.status === "succeeded") {
        setResult(job.result);
        setPhase("done");
        setBusy(false);
        dispatch({ type: "STATUS_SET", severity: "ok", text: msg("导出完成（见结果面板）") });
        return;
      }
      if (job.status === "cancelled") {
        setPhase("cancelled");
        setBusy(false);
        setJobError(null);
        return;
      }
      if (["failed", "interrupted"].includes(job.status)) {
        const err = new ApiFailure({
          status: 500,
          code: job.error?.code || "EXPORT_FAILED",
          message: job.error?.message || tr("导出任务{0}", tr("失败")),
        });
        setJobError(err);
        setPhase("error");
        setBusy(false);
        showError(dispatch, err);
        return;
      }
      setBusy(true);
      setPhase("running");
      setElapsed(Math.max(0, Math.floor((Date.now() - startedRef.current) / 1000)));
      timer = setTimeout(poll, 1500);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [jobId, state.currentId, dispatch]);

  const handleExport = async () => {
    if (!state.currentId) {
      showError(dispatch, { status: 400, code: "NO_PROJECT", message: tr("请先创建或选择一个工程") });
      return;
    }
    if (!outPath.trim()) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: tr("请填写输出文件路径") });
      return;
    }
    if (transparent && !outPath.trim().toLowerCase().endsWith(".mov")) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: tr("透明导出请使用 .mov 文件（ProRes 4444）") });
      return;
    }
    for (const [value, maximum] of [[videoBitrate, 1000000], [audioBitrate, 1536]] as const) {
      if (!transparent && value.trim() && (!Number.isInteger(Number(value)) || Number(value) <= 0 || Number(value) > maximum)) {
        showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: tr("码率需为大于 0 且不超过 {0} 的整数", maximum) });
        return;
      }
    }
    let range: { start: number; end: number } | undefined;
    if (useRange) {
      const start = Number(rangeStart);
      const end = rangeEnd.trim() ? Number(rangeEnd) : projectDuration;
      if (!Number.isFinite(start) || !Number.isFinite(end) ||
          start < 0 || end <= start || end > projectDuration) {
        showError(dispatch, {
          status: 400,
          code: "INVALID_ARGUMENT",
          message: tr("导出区间需满足 0 ≤ 开始时间 < 结束时间 ≤ {0} 秒", projectDuration.toFixed(2)),
        });
        return;
      }
      range = { start, end };
    }
    setBusy(true);
    setPhase("checking");
    setPreflight(null);
    setCheckError(null);
    setJobError(null);
    setResult(null);
    setJobId(null);
    setJob(null);
    let queued = false;
    try {
      const current = getLatestState() || state;
      const report = await preflightProject(state.currentId, current.revision, range);
      if (!aliveRef.current) return;
      setPreflight(report);
      if (!report.readyToRender) {
        setPhase("blocked");
        dispatch({ type: "STATUS_SET", severity: "warn", text: msg("导出前检查发现问题，请先修复素材或工程") });
        return;
      }
      setPhase("running");
      const out = await doExport(dispatch, getLatestState() || state, outPath.trim(), quality, range, {
        transparent,
        ...(!transparent && videoBitrate.trim() ? { videoBitrateKbps: Number(videoBitrate) } : {}),
        ...(!transparent && audioBitrate.trim() ? { audioBitrateKbps: Number(audioBitrate) } : {}),
      });
      if (out.inFlight && out.jobId) {
        queued = true;
        startedRef.current = Date.now();
        setElapsed(0);
        setJobId(out.jobId);
      } else if (out.ok && out.result) {
        setResult(out.result);
        setPhase("done");
      } else if (out.error) {
        setJobError(out.error);
        setPhase("error");
      }
    } catch (error) {
      if (!aliveRef.current) return;
      setCheckError(error instanceof Error ? error.message : String(error));
      setPhase("blocked");
    } finally {
      if (aliveRef.current && !queued) setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!jobId || cancelling) return;
    setCancelling(true);
    try {
      const updated = await cancelExportJob(jobId);
      if (!aliveRef.current) return;
      setJob(previous => previous ? { ...previous, ...updated } : null);
      if (updated.status === "cancelled") { setPhase("cancelled"); setBusy(false); setJobError(null); }
    } catch (error) { if (aliveRef.current) showError(dispatch, error); }
    finally { if (aliveRef.current) setCancelling(false); }
  };

  const locateIssue = (issue: ProjectPreflightIssue) => {
    const project = (getLatestState() || state).project;
    const clipId = issue.clipIds?.[0];
    const track = project?.sequence.tracks.find((item) => item.clips.some((clip) => clip.id === clipId));
    const clip = track?.clips.find((item) => item.id === clipId);
    if (track && clip) {
      dispatch({ type: "SELECTION_SET", selection: { trackId: track.id, clipId: clip.id } });
      dispatch({ type: "PLAYHEAD_SET", t: rationalToSecs(clip.timelineStart) });
    }
    onClose();
    window.dispatchEvent(new CustomEvent(LOCATE_PREFLIGHT_ISSUE, { detail: issue }));
  };

  const copyPath = async (path: string) => {
    try {
      await navigator.clipboard.writeText(path);
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已复制输出路径") });
    } catch {
      showError(dispatch, new ApiFailure({ status: 0, code: "CLIPBOARD", message: tr("复制失败，请手动选择路径") }));
    }
  };

  const durStr = result && !isNaN(result.duration) ? tr("{0} 秒", result.duration.toFixed(2)) : "—";

  const chooseFile = async () => {
    try {
      const name = (state.projects.find(project => project.id === state.currentId)?.name || "CutVoke").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_");
      const path = await window.cutvokeDesktop?.chooseExportFile(outPath || `${name}${transparent ? ".mov" : ".mp4"}`);
      if (path) setOutPath(path);
    } catch (error) { showError(dispatch, error); }
  };

  return (
    <>
      <Field label={tr("保存位置")}>
        <div style={{ display: "flex", gap: 8 }}>
        <TextInput
          value={outPath}
          disabled={busy}
          onChange={(e) => setOutPath(e.target.value)}
          placeholder={tr("如：D:\\视频\\我的成片.mp4")}
          aria-label={tr("输出文件路径")}
        />
        {window.cutvokeDesktop?.chooseExportFile ? <Button variant="secondary" disabled={busy || restoring} onClick={() => void chooseFile()}>
          <FolderOpen size={14} />{tr("选择保存位置")}</Button> : null}
        </div>
      </Field>
      <Field label={tr("画质")}>
        <Select value={quality} disabled={busy} onChange={(e) => setQuality(e.target.value as ExportQuality)} aria-label={tr("画质")}>
          <option value="high">{tr("高（清晰，导出较慢）")}</option>
          <option value="medium">{tr("中（画质与体积均衡）")}</option>
          <option value="low">{tr("低（体积小，适合预览）")}</option>
        </Select>
      </Field>
      <label className="export-dialog__range-toggle">
        <input type="checkbox" checked={transparent} disabled={busy} aria-label={tr("透明背景（ProRes 4444）")}
          onChange={event => {
            setTransparent(event.target.checked);
            setOutPath(path => path ? /\.[^./\\]+$/.test(path)
              ? path.replace(/\.[^./\\]+$/, event.target.checked ? ".mov" : ".mp4")
              : path + (event.target.checked ? ".mov" : ".mp4") : path);
          }} />{tr("透明背景（ProRes 4444）")}</label>
      {transparent ? <p className="cv-hint">{tr("保存为 .mov，保留透明通道；音频使用无损 PCM，文件体积较大。")}</p> : <div className="export-dialog__range-fields">
        <Field label={tr("视频码率（kbps）")}><TextInput type="number" min="1" max="1000000" step="1" value={videoBitrate} disabled={busy}
          placeholder={tr("自动（按画质）")} aria-label={tr("视频码率（kbps）")} onChange={event => setVideoBitrate(event.target.value)} /></Field>
        <Field label={tr("音频码率（kbps）")}><TextInput type="number" min="1" max="1536" step="1" value={audioBitrate} disabled={busy}
          placeholder="192" aria-label={tr("音频码率（kbps）")} onChange={event => setAudioBitrate(event.target.value)} /></Field>
      </div>}
      <label className="export-dialog__range-toggle">
        <input
          type="checkbox"
          checked={useRange}
          disabled={busy}
          onChange={(event) => setUseRange(event.target.checked)}
          aria-label={tr("仅导出时间范围")}
        />{tr("仅导出时间范围")}</label>
      {useRange ? (
        <div className="export-dialog__range-fields">
          <Field label={tr("开始时间（秒）")}>
            <TextInput
              type="number"
              min="0"
              max={projectDuration}
              step={frameDuration}
              value={rangeStart}
              disabled={busy}
              onChange={(event) => setRangeStart(event.target.value)}
              onBlur={() => setRangeStart((value) => snapToFrame(value))}
              aria-label={tr("导出开始时间")}
            />
          </Field>
          <Field label={tr("结束时间（秒；时间线 {0} 秒）", projectDuration.toFixed(2))}>
            <TextInput
              type="number"
              min="0"
              max={projectDuration}
              step={frameDuration}
              value={rangeEnd}
              disabled={busy}
              placeholder={projectDuration.toFixed(2)}
              onChange={(event) => setRangeEnd(event.target.value)}
              onBlur={() => setRangeEnd((value) => snapToFrame(value))}
              aria-label={tr("导出结束时间")}
            />
          </Field>
        </div>
      ) : null}
      {useRange ? (
        <p className="cv-hint" style={{ marginTop: 6 }}>{tr("时间会对齐到工程帧率，完成面板显示编码后的实际时长。")}</p>
      ) : null}

      <div className="export-dialog__actions">
        <Button variant="secondary" onClick={onClose}>{phase === "running" ? tr("后台导出") : tr("取消")}</Button>
        {phase === "running" && jobId ? <Button variant="secondary" onClick={() => void handleCancel()} disabled={cancelling || reconnecting}>
          {cancelling ? tr("正在取消…") : tr("取消导出")}</Button> : null}
        <Button variant="primary" onClick={handleExport} disabled={busy || restoring} aria-label={tr("开始导出")}>
          <Upload size={14} />
          {phase === "checking" ? tr("检查素材中…") : busy ? tr("导出中…") : phase === "blocked" ? tr("重新检查并导出") : tr("开始导出")}
        </Button>
      </div>

      {phase === "checking" ? (
        <div className="export-dialog__result" role="status" aria-live="polite">
          <div className="export-dialog__result-head"><Loader2 size={14} className="cv-spin" />{tr(" 检查素材中")}</div>
          <p className="cv-hint">{tr("正在检查输出范围的文件可用性与工程结构。")}</p>
        </div>
      ) : null}

      {phase === "blocked" ? (
        <div className="export-dialog__result export-dialog__result--error" role="alert">
          <div className="export-dialog__result-head"><AlertTriangle size={14} />{tr(" 导出前检查未通过")}</div>
          {checkError ? <p>{tr("导出前检查失败，请重新检查并导出。")}<br /><span dir="auto">{checkError}</span></p> : null}
          <ul className="export-preflight__issues">
            {preflight?.errors.map((issue, index) => (
              <li key={`${issue.code}-${issue.path || index}`}>
                <strong>{issue.path ? assetDisplayName(issue.path) : preflightSummary(issue)}</strong>
                {issue.path ? <p>{preflightSummary(issue)}</p> : null}
                <details><summary>{tr("原始诊断")}</summary><span dir="auto">{issue.message}</span></details>
                {issue.path ? <details><summary>{tr("文件路径")}</summary><code>{issue.path}</code></details> : null}
                {issue.path || issue.clipIds?.length ? (
                  <Button variant="secondary" size="sm" onClick={() => locateIssue(issue)}>
                    {issue.resourceKind === "media" ? tr("在素材库修复") : tr("定位问题片段")}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
          {!checkError ? <p className="cv-hint">{tr("重新链接丢失素材，或为选中的片段替换素材后，再次导出。")}</p> : null}
        </div>
      ) : null}

      {preflight && preflight.warnings.length > 0 ? (
        <div className="export-dialog__result" role="status">
          <div className="export-dialog__result-head"><AlertTriangle size={14} />{tr(" 导出前提醒")}</div>
          <ul className="export-preflight__issues">
            {preflight.warnings.map((issue, index) => <li key={`${issue.code}-${index}`}>{preflightSummary(issue)}<details><summary>{tr("原始诊断")}</summary><span dir="auto">{issue.message}</span></details></li>)}
          </ul>
        </div>
      ) : null}

      {phase === "running" ? (
        <div className="export-dialog__result">
          <div className="export-dialog__result-head">
            <Loader2 size={14} className="cv-spin" />{tr(" 导出进行中")}<Badge tone="info">{tr("进行中")}</Badge>
          </div>
          <p role="status">{job?.status === "queued" ? tr("等待队列") : job?.progress?.phase === "preparing" ? tr("准备素材与效果") : job?.progress?.phase === "rendering" ? tr("渲染片段") : job?.progress?.phase === "verifying" ? tr("验证成片") : tr("编码成片")}</p>
          {job?.progress?.fraction != null && job.status === "running" ? <>
            <progress aria-label={tr("当前阶段进度")} value={job.progress.fraction} max="1" style={{ width: "100%" }} />
            <p className="cv-hint">{tr("当前阶段 {0}%", Math.floor(job.progress.fraction * 100))}
              {!reconnecting && job.progress.etaSeconds != null && job.progress.fraction > 0 && job.progress.fraction < 1 && (!job.progress.updatedAt || Date.now()/1000-job.progress.updatedAt < 6)
                ? tr("，预计剩余 {0}（当前阶段）", clockTime(job.progress.etaSeconds)) : null}</p>
          </> : null}
          <p className="cv-hint" style={{ marginTop: 6 }}>{tr("已用时 {0}。可以关闭此面板继续编辑，重新打开可查看结果；请保持软件运行。", `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`)}</p>
          {jobId ? (
            <div className="inspector__row">
              <span className="inspector__key">{tr("任务")}</span>
              <span className="inspector__val cv-mono">{jobId}</span>
            </div>
          ) : null}
        </div>
      ) : null}

      {reconnecting ? <p className="cv-hint" role="status">{tr("正在重新连接导出任务…")}</p> : null}

      {phase === "cancelled" ? <div className="export-dialog__result" role="status"><div className="export-dialog__result-head">{tr("导出已取消")}</div><p className="cv-hint">{tr("任务已停止，未发布成片。")}</p></div> : null}

      {phase === "error" && jobError ? (
        <div className="export-dialog__result export-dialog__result--error">
          <div className="export-dialog__result-head">
            <AlertTriangle size={14} />{tr(" 导出失败")}<Badge tone="err">{tr("失败")}</Badge>
          </div>
          <pre className="error-modal__detail" style={{ marginTop: 6 }}>
            [{jobError.code}] {jobError.message}
          </pre>
        </div>
      ) : null}

      {phase === "done" && result ? (
        <div className="export-dialog__result">
          <div className="export-dialog__result-head">
            <CheckCircle2 size={14} />{tr(" 导出完成")}<Badge tone="ok">{tr("已完成")}</Badge>
          </div>
          <div className="inspector__row">
            <span className="inspector__key">{tr("输出")}</span>
            <span className="inspector__val cv-mono">{result.output_path}</span>
          </div>
          <div className="export-dialog__actions" style={{ marginTop: 6 }}>
            {window.cutvokeDesktop?.revealExport && jobId && state.currentId ? <Button variant="secondary" size="sm" onClick={() => {
              void window.cutvokeDesktop!.revealExport(state.currentId!, jobId).catch(error => showError(dispatch, error));
            }}><FolderOpen size={13} />{tr("在文件夹中显示")}</Button> : null}
            <Button variant="secondary" size="sm" onClick={() => copyPath(result.output_path)} aria-label={tr("复制输出路径")}>
              <Copy size={13} />{tr(" 复制路径")}</Button>
          </div>
          <div className="inspector__row" style={{ marginTop: 6 }}>
            <span className="inspector__key">{tr("时长")}</span>
            <span className="inspector__val">{durStr}</span>
          </div>
          <div className="inspector__row">
            <span className="inspector__key">{tr("尺寸")}</span>
            <span className="inspector__val">
              {result.width}×{result.height}
            </span>
          </div>
          {result.has_audio !== undefined && (
            <div className="inspector__row">
              <span className="inspector__key">{tr("含音频")}</span>
              <span className="inspector__val">{result.has_audio ? tr("是") : tr("否")}</span>
            </div>
          )}
          {result.warnings && result.warnings.length > 0 && (
            <pre className="error-modal__detail" style={{ marginTop: 6 }}>
              {result.warnings.join("\n")}
            </pre>
          )}
          {!window.cutvokeDesktop ? <p className="cv-hint" style={{ marginTop: 6 }}>{tr("Web 端暂无法直接打开资源管理器，已提供“复制路径”，可在文件管理器中粘贴定位。")}</p> : null}
        </div>
      ) : null}
    </>
  );
}
