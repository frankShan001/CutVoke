import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useEditor } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { enqueueExportProject, listExportJobs, sendCommand, type ExportJob } from "../lib/api";
import { rationalToSecs } from "../lib/rational";
import "./ProductionTools.css";
import { flushPendingEdits } from "../lib/pendingEdits";

export function ProductionTools() {
  const { state, dispatch } = useEditor({ subscribeToClock: true });
  const sequence = state.project?.sequence;
  const [width, setWidth] = useState(1920), [height, setHeight] = useState(1080), [fps, setFps] = useState("30");
  const [reframe, setReframe] = useState("contain"), [name, setName] = useState("");
  const [prefix, setPrefix] = useState(""), [queue, setQueue] = useState<ExportJob[]>([]);
  const [format, setFormat] = useState("otio"), [content, setContent] = useState(""), [strict, setStrict] = useState(true);
  const [report, setReport] = useState(""), [busy, setBusy] = useState(false), [monitor, setMonitor] = useState<number | null>(null);
  const cameras = (sequence?.tracks || []).filter(track => track.kind === "video" && track.role !== "sticker");
  const [reference, setReference] = useState(""), [target, setTarget] = useState("");
  useEffect(() => {
    if (!sequence) return;
    setWidth(sequence.width); setHeight(sequence.height); setFps(String(rationalToSecs(sequence.fps)));
    setReframe(sequence.reframe || "stretch"); setName(sequence.name || "");
  }, [sequence?.id, sequence?.width, sequence?.height, sequence?.fps.num, sequence?.fps.den, sequence?.reframe]);
  const run = async (type: string, payload: Record<string, unknown>) => {
    setBusy(true); setReport("");
    try {
      if (!await flushPendingEdits()) return;
      const result = await runCommand(dispatch, getLatestState() || state, type, payload);
      setReport(result.ok ? JSON.stringify(result.command?.changedEntities, null, 2) : result.error?.message || "");
    } finally { setBusy(false); }
  };
  const batch = async () => {
    if (!state.currentId || !prefix.trim()) return;
    setBusy(true);
    try {
      if (!await flushPendingEdits()) return;
      const current = getLatestState() || state;
      if (current.currentId !== state.currentId || current.editLock) return;
      for (const [index, item] of (current.project?.sequences || []).entries()) {
        const suffix = `${index + 1}_${item.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
        await enqueueExportProject(state.currentId, `${prefix.replace(/\.mp4$/i, "")}_${suffix}.mp4`, "high",
          undefined, false, { sequenceId: item.id });
      }
      setQueue(await listExportJobs(state.currentId));
    } catch (cause) { setReport(String(cause)); } finally { setBusy(false); }
  };
  useEffect(() => {
    if (!state.currentId || !queue.some(job => ["queued", "running"].includes(job.status))) return;
    const timer = window.setInterval(() => { listExportJobs(state.currentId!).then(setQueue).catch(() => {}); }, 1000);
    return () => clearInterval(timer);
  }, [state.currentId, queue]);
  const interchange = async () => {
    if (!state.currentId) return;
    setBusy(true);
    try {
      if (!await flushPendingEdits()) return;
      const current = getLatestState() || state;
      if (current.currentId !== state.currentId || current.editLock) return;
      const result = await sendCommand(state.currentId, "project.interchangeExport", { format, strict }, current.revision, crypto.randomUUID());
      const item = result.changedEntities[0] as { content?: string; diagnostics?: unknown };
      if (item.content) {
        setContent(item.content);
        const url = URL.createObjectURL(new Blob([item.content], { type: "text/plain;charset=utf-8" }));
        const link = document.createElement("a"); link.href = url; link.download = `${state.currentId}.${format}`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      setReport(JSON.stringify(item.diagnostics || [], null, 2));
    } catch (cause) { setReport(String(cause)); } finally { setBusy(false); }
  };
  if (!sequence) return null;
  const disabled = busy || Boolean(state.editLock);
  return <div className="cv-production-tools">
    <details><summary>{tr("画布与制作版本")}</summary>
      <label>{tr("活动序列")}<select aria-label={tr("活动序列")} value={sequence.id} disabled={disabled}
        onChange={event => void run("sequence.switch", { sequenceId: event.target.value })}>
        {(state.project?.sequences || [sequence]).map(item => <option key={item.id} value={item.id}>
          {item.name || item.id} · {item.width}×{item.height}</option>)}
      </select></label>
      <label>{tr("版本名称")}<input aria-label={tr("版本名称")} value={name} onChange={event => setName(event.target.value)} /></label>
      <label>{tr("画布宽度")}<input aria-label={tr("画布宽度")} type="number" value={width} onChange={event => setWidth(Number(event.target.value))} /></label>
      <label>{tr("画布高度")}<input aria-label={tr("画布高度")} type="number" value={height} onChange={event => setHeight(Number(event.target.value))} /></label>
      <label>{tr("输出帧率")}<input aria-label={tr("输出帧率")} value={fps} onChange={event => setFps(event.target.value)} /></label>
      <select aria-label={tr("重构图方式")} value={reframe} onChange={event => setReframe(event.target.value)}>
        <option value="contain">{tr("完整保留画面")}</option><option value="cover">{tr("填满并居中裁切")}</option>
        <option value="stretch">{tr("拉伸到画布")}</option></select>
      <button disabled={disabled} onClick={() => void run("sequence.configure", { width, height, fps: Number(fps), reframe, name })}>{tr("应用画布设置")}</button>
      <button disabled={disabled} onClick={() => void run("sequence.duplicate", { width, height, fps: Number(fps), reframe, name })}>{tr("复制为制作版本")}</button>
      <label>{tr("批量输出路径前缀")}<input aria-label={tr("批量输出路径前缀")} value={prefix} onChange={event => setPrefix(event.target.value)} /></label>
      <button disabled={disabled || !prefix.trim()} onClick={() => void batch()}>{tr("全部版本排队导出")}</button>
      <ul>{queue.map(job => <li key={job.jobId}>{job.outPath} · {job.status}</li>)}</ul>
    </details>
    <details><summary>{tr("多机位剪辑")}</summary>
      {!sequence.multicam ? <button disabled={disabled || cameras.length < 2}
        onClick={() => void run("multicam.create", { trackIds: cameras.map(track => track.id) })}>{tr("建立多机位组")}</button> : <>
        <button onClick={() => setMonitor(state.playhead)}>{tr("更新机位监看")}</button>
        <p>{tr("监看与切点使用当前播放头时间")}: {state.playhead.toFixed(3)} s</p>
        {cameras.filter(track => sequence.multicam?.trackIds?.includes(track.id)).map(track => <div key={track.id}>
          <strong>{track.id}</strong>{monitor != null ? <img width={180} alt={`${tr("机位监看")} ${track.id}`}
            onError={() => setReport(tr("机位在该时刻无可用画面"))}
            src={`/api/v1/projects/${encodeURIComponent(state.currentId!)}/multicam-monitor?trackId=${encodeURIComponent(track.id)}&time=${monitor}`} /> : null}
          <button disabled={disabled} onClick={() => void run("multicam.switch", { trackId: track.id, time: state.playhead })}>{tr("在播放头切换机位")}</button>
        </div>)}
        <ol>{sequence.multicam.cuts?.map((cut, index) => <li key={index}>{rationalToSecs(cut.time).toFixed(3)} s → {cut.trackId}</li>)}</ol>
        <label>{tr("基准机位")}<select aria-label={tr("基准机位")} value={reference} onChange={event => setReference(event.target.value)}>
          <option value="" />{cameras.map(track => <option key={track.id}>{track.id}</option>)}</select></label>
        <label>{tr("待同步机位")}<select aria-label={tr("待同步机位")} value={target} onChange={event => setTarget(event.target.value)}>
          <option value="" />{cameras.map(track => <option key={track.id}>{track.id}</option>)}</select></label>
        <button disabled={disabled || !reference || !target || reference === target}
          onClick={() => void run("multicam.sync", { referenceTrackId: reference, targetTrackId: target })}>{tr("按音频波形同步")}</button>
        <button disabled={disabled} onClick={() => void run("multicam.remove", {})}>{tr("移除多机位组")}</button>
      </>}
    </details>
    <details><summary>{tr("工程互换")}</summary>
      <select aria-label={tr("互换格式")} value={format} onChange={event => setFormat(event.target.value)}>
        <option value="otio">OTIO JSON</option><option value="edl">CMX 3600 EDL</option><option value="fcpxml">FCPXML 1.10</option>
      </select>
      <label><input type="checkbox" checked={strict} onChange={event => setStrict(event.target.checked)} />{tr("拒绝有损互换")}</label>
      <button disabled={disabled} onClick={() => void interchange()}>{tr("下载互换文件")}</button>
      <input type="file" accept=".otio,.json,.edl,.fcpxml,.xml" aria-label={tr("选择互换文件")}
        onChange={event => { const file = event.target.files?.[0]; if (file) void file.text().then(setContent); }} />
      <textarea aria-label={tr("互换文件内容")} value={content} onChange={event => setContent(event.target.value)} />
      <button disabled={disabled || !content.trim()} onClick={() => void run("project.interchangeImport", { format, content, strict })}>{tr("导入为新序列")}</button>
      <p>{tr("仅支持已声明的剪切子集；不支持 AAF，未映射项目会返回诊断。")}</p>
    </details>
    {report ? <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }} role="status">{report}</pre> : null}
  </div>;
}
