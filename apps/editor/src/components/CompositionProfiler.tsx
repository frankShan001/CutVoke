import { useState } from "react";
import { tr, useLocale } from "../i18n";
import { useEditor } from "../store/editor";
import { getLatestState } from "../store/actions";
import { sendCommand } from "../lib/api";
import { flushPendingEdits } from "../lib/pendingEdits";

interface Report { width: number; height: number; fps: number; start: number; duration: number; runs: {
  run: string; processor: string; timings: Record<string, number>;
  effects: { clipId: string; instanceId: string; effectId: string; samples: number; filterIntervalSeconds: number; maxSeconds: number }[];
  procedural: { clipId: string; effectId: string; generationSeconds?: number; generatedFrames?: number; memoryHit?: boolean; diskHit?: boolean }[];
}[] }
export function CompositionProfiler() {
  useLocale();
  const { state } = useEditor({ subscribeToClock: false });
  const [owner, setOwner] = useState<string | null>(null), [revision, setRevision] = useState("");
  const [report, setReport] = useState<Report | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [duration, setDuration] = useState(2), [maximum, setMaximum] = useState(640);
  async function measure() {
    if (!state.currentId || !await flushPendingEdits()) return;
    const current = getLatestState() || state;
    if (current.currentId !== state.currentId) return;
    setBusy(true); setError(""); setOwner(current.currentId); setRevision(current.revision);
    try {
      const result = await sendCommand(current.currentId!, "composition.profile", { start: current.playhead, duration, maximum }, current.revision, crypto.randomUUID());
      setReport(result.changedEntities[0] as unknown as Report);
    } catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  }
  return <details className="composition-profiler"><summary>{tr("合成性能分析")}</summary>
    <label>{tr("采样时长")}<input className="cv-input" type="number" min={.1} max={5} step={.1} value={duration} onChange={event => setDuration(Number(event.target.value))} /></label>
    <label>{tr("预览尺寸")}<select className="cv-input" value={maximum} onChange={event => setMaximum(Number(event.target.value))}>{[320, 640, 960, 1280].map(value => <option key={value}>{value}</option>)}</select></label>
    <button type="button" className="cv-btn cv-btn--sm" disabled={busy || !state.currentId || duration < .1 || duration > 5} onClick={() => void measure()}>{busy ? tr("正在分析…") : tr("分析播放头区间")}</button>
    {error && owner === state.currentId ? <p role="alert">{error}</p> : null}
    {report && owner === state.currentId ? <>
      <p className="cv-hint">{report.width}×{report.height} · {report.fps} fps · {report.start.toFixed(2)}–{(report.start + report.duration).toFixed(2)} s</p>
      {revision !== state.revision ? <p className="cv-hint">{tr("工程已修改，以上是旧版本的分析。")}</p> : null}
      <p className="cv-hint">{tr("滤镜区间包含调度等待，可重叠；不是独占 CPU 时间，不能相加当作总渲染时间。")}</p>
      {report.runs.map((run, index) => <div key={run.run}><strong>{index ? tr("重复运行") : tr("首次运行")}</strong><p>{tr("总耗时")} {run.timings.totalRenderSeconds?.toFixed(3)} s · {run.processor}</p>
        <p className="cv-hint">{tr("准备")} {run.timings.preparationSeconds?.toFixed(3)} s · {tr("内存缓存命中")} {run.timings.lightMemoryCacheHits || 0} · {tr("磁盘缓存命中")} {run.timings.lightDiskCacheHits || 0}</p>
        {run.effects.map(item => <div key={`${item.clipId}:${item.instanceId}`} className="cv-hint"><strong>{item.clipId} / {item.effectId}</strong><br />{tr("滤镜采样区间")} {(item.filterIntervalSeconds * 1000).toFixed(2)} ms · {item.samples} {tr("帧")} · {tr("最大单帧")} {(item.maxSeconds * 1000).toFixed(2)} ms</div>)}
        {run.procedural.map((item, i) => <p key={i} className="cv-hint">{item.clipId} / {item.effectId}: {tr("生成工作线程")} {item.generationSeconds?.toFixed(3) || 0} s · {item.generatedFrames || 0} {tr("帧")}</p>)}
      </div>)}
    </> : null}
  </details>;
}
