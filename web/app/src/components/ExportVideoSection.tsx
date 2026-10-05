/** 导出成片区块（X02）：保存位置 + 画质 → 开始导出 → 任务状态（进行中/完成/失败）。
    后端无百分比进度字段，按 GET /exports 任务状态轮询；202 in_flight 时进入轮询。 */

import { useEffect, useRef, useState } from "react";
import { Upload, Copy, Loader2, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Button, Field, Select, TextInput, Badge } from "./ui";
import { useEditor, showError } from "../store/editor";
import { doExport, getLatestState } from "../store/actions";
import { listExportJobs, preflightProject, ApiFailure, type ExportQuality, type ExportJob, type ProjectPreflightIssue, type ProjectPreflightReport } from "../lib/api";
import { assetDisplayName, useSessionAssets } from "../lib/assetStore";
import { LOCATE_PREFLIGHT_ISSUE } from "../lib/preflight";
import { rationalToSecs } from "../lib/rational";
import type { ExportResult } from "../types/api";

export function ExportVideoSection({ onClose }: { onClose: () => void }) {
  const { state, dispatch } = useEditor();
  useSessionAssets();
  const [outPath, setOutPath] = useState("");
  const [quality, setQuality] = useState<ExportQuality>("high");
  const [useRange, setUseRange] = useState(false);
  const [rangeStart, setRangeStart] = useState("0");
  const [rangeEnd, setRangeEnd] = useState("");
  const [result, setResult] = useState<ExportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<"idle" | "checking" | "blocked" | "running" | "done" | "error">("idle");
  const [preflight, setPreflight] = useState<ProjectPreflightReport | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [jobError, setJobError] = useState<ApiFailure | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
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
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  /** 轮询导出任务直到终态（后端无百分比进度，仅 running/succeeded/failed）。
      仅在 202 in_flight 或网络抖动时调用；同步 200 直接走 done 分支。 */
  const pollJob = async (id: string) => {
    const deadline = Date.now() + 180_000;
    while (aliveRef.current && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 1500));
      if (!aliveRef.current || !state.currentId) return;
      let jobs: ExportJob[] = [];
      try {
        jobs = await listExportJobs(state.currentId);
      } catch {
        /* 轮询失败则继续，直到超时 */
      }
      const job = jobs.find((j) => j.jobId === id);
      if (!job) continue;
      if (job.status === "succeeded") {
        setResult(job.result);
        setPhase("done");
        dispatch({ type: "STATUS_SET", severity: "ok", text: "导出完成（见结果面板）" });
        return;
      }
      if (["failed", "cancelled", "interrupted"].includes(job.status)) {
        const err = new ApiFailure({
          status: 500,
          code: job.error?.code || "EXPORT_FAILED",
          message: job.error?.message || `导出任务${job.status === "cancelled" ? "已取消" : "失败"}`,
        });
        setJobError(err);
        setPhase("error");
        showError(dispatch, err);
        return;
      }
    }
    if (!aliveRef.current) return;
    const to = new ApiFailure({ status: 0, code: "POLL_TIMEOUT", message: "轮询导出状态超时，可重新打开导出抽屉查看结果" });
    setJobError(to);
    setPhase("error");
    showError(dispatch, to);
  };

  const handleExport = async () => {
    if (!state.currentId) {
      showError(dispatch, { status: 400, code: "NO_PROJECT", message: "请先创建或选择一个工程" });
      return;
    }
    if (!outPath.trim()) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: "请填写输出文件路径" });
      return;
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
          message: `导出区间需满足 0 ≤ 开始时间 < 结束时间 ≤ ${projectDuration.toFixed(2)} 秒`,
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
    try {
      const current = getLatestState() || state;
      const report = await preflightProject(state.currentId, current.revision, range);
      if (!aliveRef.current) return;
      setPreflight(report);
      if (!report.readyToRender) {
        setPhase("blocked");
        dispatch({ type: "STATUS_SET", severity: "warn", text: "导出前检查发现问题，请先修复素材或工程" });
        return;
      }
      setPhase("running");
      const out = await doExport(dispatch, getLatestState() || state, outPath.trim(), quality, range);
      if (out.inFlight && out.jobId) {
        setJobId(out.jobId);
        await pollJob(out.jobId);
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
      if (aliveRef.current) setBusy(false);
    }
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
      dispatch({ type: "STATUS_SET", severity: "ok", text: "已复制输出路径" });
    } catch {
      showError(dispatch, new ApiFailure({ status: 0, code: "CLIPBOARD", message: "复制失败，请手动选择路径" }));
    }
  };

  const durStr = result && !isNaN(result.duration) ? `${result.duration.toFixed(2)} 秒` : "—";

  return (
    <>
      <Field label="保存位置">
        <TextInput
          value={outPath}
          disabled={busy}
          onChange={(e) => setOutPath(e.target.value)}
          placeholder="如：D:\视频\我的成片.mp4"
          aria-label="输出文件路径"
        />
      </Field>
      <Field label="画质">
        <Select value={quality} disabled={busy} onChange={(e) => setQuality(e.target.value as ExportQuality)} aria-label="画质">
          <option value="high">高（清晰，导出较慢）</option>
          <option value="low">低（体积小，适合预览）</option>
        </Select>
      </Field>
      <label className="export-dialog__range-toggle">
        <input
          type="checkbox"
          checked={useRange}
          disabled={busy}
          onChange={(event) => setUseRange(event.target.checked)}
          aria-label="仅导出时间范围"
        />
        仅导出时间范围
      </label>
      {useRange ? (
        <div className="export-dialog__range-fields">
          <Field label="开始时间（秒）">
            <TextInput
              type="number"
              min="0"
              max={projectDuration}
              step={frameDuration}
              value={rangeStart}
              disabled={busy}
              onChange={(event) => setRangeStart(event.target.value)}
              onBlur={() => setRangeStart((value) => snapToFrame(value))}
              aria-label="导出开始时间"
            />
          </Field>
          <Field label={`结束时间（秒；时间线 ${projectDuration.toFixed(2)} 秒）`}>
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
              aria-label="导出结束时间"
            />
          </Field>
        </div>
      ) : null}
      {useRange ? (
        <p className="cv-hint" style={{ marginTop: 6 }}>
          时间会对齐到工程帧率，完成面板显示编码后的实际时长。
        </p>
      ) : null}

      <div className="export-dialog__actions">
        <Button variant="secondary" onClick={onClose} disabled={busy}>
          取消
        </Button>
        <Button variant="primary" onClick={handleExport} disabled={busy} aria-label="开始导出">
          <Upload size={14} />
          {phase === "checking" ? "检查素材中…" : busy ? "导出中…" : phase === "blocked" ? "重新检查并导出" : "开始导出"}
        </Button>
      </div>

      {phase === "checking" ? (
        <div className="export-dialog__result" role="status" aria-live="polite">
          <div className="export-dialog__result-head"><Loader2 size={14} className="cv-spin" /> 检查素材中</div>
          <p className="cv-hint">正在检查输出范围的文件可用性与工程结构。</p>
        </div>
      ) : null}

      {phase === "blocked" ? (
        <div className="export-dialog__result export-dialog__result--error" role="alert">
          <div className="export-dialog__result-head"><AlertTriangle size={14} /> 导出前检查未通过</div>
          {checkError ? <p>{checkError}。请点击“重新检查并导出”重试。</p> : null}
          <ul className="export-preflight__issues">
            {preflight?.errors.map((issue, index) => (
              <li key={`${issue.code}-${issue.path || index}`}>
                <strong>{issue.path ? assetDisplayName(issue.path) : issue.message}</strong>
                {issue.path ? <p>{issue.message}</p> : null}
                {issue.path ? <details><summary>文件路径</summary><code>{issue.path}</code></details> : null}
                {issue.path || issue.clipIds?.length ? (
                  <Button variant="secondary" size="sm" onClick={() => locateIssue(issue)}>
                    {issue.resourceKind === "media" ? "在素材库修复" : "定位问题片段"}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
          {!checkError ? <p className="cv-hint">重新链接丢失素材，或为选中的片段替换素材后，再次导出。</p> : null}
        </div>
      ) : null}

      {preflight && preflight.warnings.length > 0 ? (
        <div className="export-dialog__result" role="status">
          <div className="export-dialog__result-head"><AlertTriangle size={14} /> 导出前提醒</div>
          <ul className="export-preflight__issues">
            {preflight.warnings.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}
          </ul>
        </div>
      ) : null}

      {phase === "running" ? (
        <div className="export-dialog__result">
          <div className="export-dialog__result-head">
            <Loader2 size={14} className="cv-spin" /> 导出进行中
            <Badge tone="info">进行中</Badge>
          </div>
          <p className="cv-hint" style={{ marginTop: 6 }}>
            导出任务在后台串行处理，按任务状态轮询；完成后自动更新结果。
          </p>
          {jobId ? (
            <div className="inspector__row">
              <span className="inspector__key">任务</span>
              <span className="inspector__val cv-mono">{jobId}</span>
            </div>
          ) : null}
        </div>
      ) : null}

      {phase === "error" && jobError ? (
        <div className="export-dialog__result export-dialog__result--error">
          <div className="export-dialog__result-head">
            <AlertTriangle size={14} /> 导出失败
            <Badge tone="err">失败</Badge>
          </div>
          <pre className="error-modal__detail" style={{ marginTop: 6 }}>
            [{jobError.code}] {jobError.message}
          </pre>
        </div>
      ) : null}

      {phase === "done" && result ? (
        <div className="export-dialog__result">
          <div className="export-dialog__result-head">
            <CheckCircle2 size={14} /> 导出完成
            <Badge tone="ok">已完成</Badge>
          </div>
          <div className="inspector__row">
            <span className="inspector__key">输出</span>
            <span className="inspector__val cv-mono">{result.output_path}</span>
          </div>
          <div className="export-dialog__actions" style={{ marginTop: 6 }}>
            <Button variant="secondary" size="sm" onClick={() => copyPath(result.output_path)} aria-label="复制输出路径">
              <Copy size={13} /> 复制路径
            </Button>
          </div>
          <div className="inspector__row" style={{ marginTop: 6 }}>
            <span className="inspector__key">时长</span>
            <span className="inspector__val">{durStr}</span>
          </div>
          <div className="inspector__row">
            <span className="inspector__key">尺寸</span>
            <span className="inspector__val">
              {result.width}×{result.height}
            </span>
          </div>
          {result.has_audio !== undefined && (
            <div className="inspector__row">
              <span className="inspector__key">含音频</span>
              <span className="inspector__val">{result.has_audio ? "是" : "否"}</span>
            </div>
          )}
          {result.warnings && result.warnings.length > 0 && (
            <pre className="error-modal__detail" style={{ marginTop: 6 }}>
              {result.warnings.join("\n")}
            </pre>
          )}
          <p className="cv-hint" style={{ marginTop: 6 }}>
            Web 端暂无法直接打开资源管理器，已提供“复制路径”，可在文件管理器中粘贴定位。
          </p>
        </div>
      ) : null}
    </>
  );
}
