import { tr } from "../i18n";
import { useEditor } from "../store/editor";
import { savePreviewBudget, usePreviewBudget } from "../lib/previewBudget";
export function PreviewBudgetSettings() {
  const { state } = useEditor({ subscribeToClock: false });
  const value = usePreviewBudget();
  const range = value.range?.projectId === state.currentId ? value.range : undefined;
  return <section className="settings-section"><h3>{tr("空闲预览预算")}</h3>
    <label><input type="checkbox" checked={value.enabled} onChange={event => savePreviewBudget({ ...value, enabled: event.target.checked })} />{tr("允许空闲预热")}</label>
    <label><input type="checkbox" checked={value.continuous} onChange={event => savePreviewBudget({ ...value, continuous: event.target.checked })} />{tr("准备连续预览")}</label>
    <label>{tr("提前准备秒数")}<input type="number" min={0} max={30} step={2} value={value.aheadSeconds} onChange={event => { if (event.target.validity.valid) savePreviewBudget({ ...value, aheadSeconds: Number(event.target.value) }); }} /></label>
    <label>{tr("空闲延迟（毫秒）")}<input type="number" min={200} max={10000} step={100} value={value.delayMs} onChange={event => { if (event.target.validity.valid) savePreviewBudget({ ...value, delayMs: Number(event.target.value) }); }} /></label>
    <label>{tr("每次预热时间预算（秒）")}<input type="number" min={1} max={120} value={value.wallSeconds} onChange={event => { if (event.target.validity.valid) savePreviewBudget({ ...value, wallSeconds: Number(event.target.value) }); }} /></label>
    <label><input type="checkbox" checked={!!range} disabled={!state.currentId} onChange={event => savePreviewBudget({ ...value, range: event.target.checked ? { projectId: state.currentId!, start: state.playhead, end: state.playhead + 10 } : undefined })} />{tr("限定当前工程预热范围")}</label>
    {range ? <div>{([['start', tr("开始")], ['end', tr("结束")]] as const).map(([key, label]) => <label key={key}>{label}<input type="number" min={0} value={range[key]} step={.1}
      onChange={event => { const next = { ...range, [key]: Number(event.target.value) }; if (next.end > next.start && next.start >= 0) savePreviewBudget({ ...value, range: next }); }} /></label>)}</div> : null}
    <p className="cache-settings__muted">{tr("预算用尽即停止本次后台任务；播放和定位仍按需准备。修改工程或切换工程会取消旧任务。")}</p>
  </section>;
}
