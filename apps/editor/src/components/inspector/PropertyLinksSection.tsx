import { useEffect, useState } from "react";
import { tr } from "../../i18n";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import { assetDisplayName } from "../../lib/assetStore";
import type { Clip } from "../../types/api";
export function PropertyLinksSection({ clip }: { clip: Clip }) {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [target, setTarget] = useState(clip.role === "adjustment" ? "opacity" : "x"), [kind, setKind] = useState("property"), [source, setSource] = useState("");
  useEffect(() => setTarget(clip.role === "adjustment" ? "opacity" : "x"), [clip.id, clip.role]);
  const [sourceParam, setSourceParam] = useState("x"), [scale, setScale] = useState(1), [offset, setOffset] = useState(0);
  const [amplitude, setAmplitude] = useState(50), [frequency, setFrequency] = useState(1), [busy, setBusy] = useState(false);
  const parameters = { x: "X", y: "Y", scale: tr("缩放"), rotation: tr("旋转"), opacity: tr("透明度") };
  const choices = state.project?.sequence.tracks.filter(track => track.kind === "video" || track.kind === "text").flatMap(track => track.clips) || [];
  async function bind(param: string, binding: unknown) {
    setBusy(true);
    try { await runCommand(dispatch, getLatestState() || state, "clip.bind", { clipId: clip.id, param, binding }); }
    finally { setBusy(false); }
  }
  const options = Object.entries(parameters).filter(([key]) => clip.role !== "adjustment" || key === "opacity").map(([key, name]) => <option value={key} key={key}>{name}</option>);
  return <details className="inspector__section"><summary>{tr("参数绑定")}</summary><fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0 }}>
    {Object.entries(clip.propertyLinks || {}).map(([param, value]) => <div key={param} className="inspector__subrow"><span>{param} ← {value.source.clipId || value.source.type}</span><button type="button" className="cv-btn cv-btn--sm" onClick={() => void bind(param, null)}>{tr("解除绑定")}</button></div>)}
    <label className="inspector__subrow"><span>{tr("目标属性")}</span><select className="cv-input" value={target} onChange={event => setTarget(event.target.value)}>{options}</select></label>
    <label className="inspector__subrow"><span>{tr("来源")}</span><select className="cv-input" value={kind} onChange={event => setKind(event.target.value)}><option value="property">{tr("属性")}</option><option value="sine">{tr("正弦控制器")}</option><option value="time">{tr("时间")}</option><option value="constant">{tr("固定值")}</option></select></label>
    {kind === "property" ? <><select className="cv-input" aria-label={tr("来源片段")} value={source} onChange={event => setSource(event.target.value)}><option value="">{tr("选择片段")}</option>{choices.map(item => <option key={item.id} value={item.id}>{assetDisplayName(item.assetRef) || item.id} · {item.id}</option>)}</select>
      <select className="cv-input" aria-label={tr("来源属性")} value={sourceParam} onChange={event => setSourceParam(event.target.value)}>{options}</select></> : null}
    {kind === "sine" ? <><label className="inspector__subrow">{tr("振幅")}<input type="number" className="cv-input" value={amplitude} min={-100000} max={100000} onChange={event => setAmplitude(Number(event.target.value))} /></label><label className="inspector__subrow">{tr("频率")}<input type="number" className="cv-input" min={-100} max={100} value={frequency} step={.1} onChange={event => setFrequency(Number(event.target.value))} /></label></> : null}
    <label className="inspector__subrow">{tr("倍率")}<input type="number" className="cv-input" value={scale} min={-10000} max={10000} step={.1} onChange={event => setScale(Number(event.target.value))} /></label>
    <label className="inspector__subrow">{tr("偏移")}<input type="number" className="cv-input" value={offset} min={-100000} max={100000} onChange={event => setOffset(Number(event.target.value))} /></label>
    <button type="button" className="cv-btn cv-btn--sm" disabled={kind === "property" && !source} onClick={() => void bind(target, {
      source: kind === "property" ? { type: kind, clipId: source, param: sourceParam } : { type: kind, ...(kind === "sine" ? { amplitude, frequency } : {}) }, scale, offset })}>{tr("应用绑定")}</button>
  </fieldset></details>;
}
