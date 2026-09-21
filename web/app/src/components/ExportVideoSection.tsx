/** 导出成片区块（X02）：保存位置 + 画质 → 开始导出 → 任务状态（进行中/完成/失败）。
    后端无百分比进度字段，按 GET /exports 任务状态轮询；202 in_flight 时进入轮询。 */

import { useEffect, useRef, useState } from "react";
import { Upload, Copy, Loader2, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Button, Field, Select, TextInput, Badge } from "./ui";
import { useEditor, showError } from "../store/editor";
import { doExport } from "../store/actions";
import { listExportJobs, ApiFailure, type ExportQuality, type ExportJob } from "../lib/api";
import type { ExportResult } from "../types/api";

export function ExportVideoSection({ onClose }: { onClose: () => void }) {
  const { state, dispatch } = useEditor();
  const [outPath, setOutPath] = useState("");
  const [quality, setQuality] = useState<ExportQuality>("high");
  const [result, setResult] = useState<ExportResult | null>(null);
  const [busy, setBusy] = useState(false);
  /** 导出任务阶段：idle 待提交 / running 进行中 / done 完成 / error 失败。 */
  const [phase, setPhase] = useState<"idle" | "running" | "done" | "error">("idle");
  const [jobError, setJobError] = useState<ApiFailure | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
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
      if (job.status === "failed") {
        const err = new ApiFailure({
          status: 500,
          code: job.error?.code || "EXPORT_FAILED",
          message: job.error?.message || "导出失败（后端未返回详情）",
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
    setBusy(true);
    setPhase("running");
    setJobError(null);
    setResult(null);
    const out = await doExport(dispatch, state, outPath.trim(), quality);
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
    setBusy(false);
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
          onChange={(e) => setOutPath(e.target.value)}
          placeholder="如：D:\视频\我的成片.mp4"
          aria-label="输出文件路径"
        />
      </Field>
      <Field label="画质">
        <Select value={quality} onChange={(e) => setQuality(e.target.value as ExportQuality)} aria-label="画质">
          <option value="high">高（清晰，导出较慢）</option>
          <option value="low">低（体积小，适合预览）</option>
        </Select>
      </Field>

      <div className="export-dialog__actions">
        <Button variant="secondary" onClick={onClose} disabled={busy}>
          取消
        </Button>
        <Button variant="primary" onClick={handleExport} disabled={busy} aria-label="开始导出">
          <Upload size={14} />
          {busy ? "导出中…" : "开始导出"}
        </Button>
      </div>

      {phase === "running" ? (
        <div className="export-dialog__result">
          <div className="export-dialog__result-head">
            <Loader2 size={14} className="cv-spin" /> 导出进行中
            <Badge tone="info">进行中</Badge>
          </div>
          <p className="cv-hint" style={{ marginTop: 6 }}>
            后端无百分比进度字段，按任务状态（进行中 / 已完成 / 失败）轮询；导出为同步渲染，结束后自动更新结果。
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
