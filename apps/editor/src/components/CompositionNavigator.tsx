import { useEffect, useRef, useState } from "react";
import { tr, useLocale } from "../i18n";
import { useEditor } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { rationalToSecs, secsToRational } from "../lib/rational";
import { assetDisplayName } from "../lib/assetStore";
import { flushPendingEdits } from "../lib/pendingEdits";
import type { Clip, Sequence } from "../types/api";
import "../styles/compositions.css";

export function CompositionNavigator() {
  useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [owner, setOwner] = useState<string | null>(null), [path, setPath] = useState<string[]>([]);
  const [time, setTime] = useState(0), [selected, setSelected] = useState<string | null>(null);
  const [frame, setFrame] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null), serial = useRef(0), positions = useRef(new Map<string, number>());
  const client = useRef("composition_" + crypto.randomUUID());
  let sequence: Sequence | undefined = state.project?.sequence;
  const trail: { name: string; sequence: Sequence }[] = sequence ? [{ name: sequence.name || tr("主合成"), sequence }] : [];
  for (const id of path) {
    const clip: Clip | undefined = sequence?.tracks.flatMap(track => track.clips).find(clip => clip.id === id);
    sequence = clip?.nested || state.project?.sequences?.find(item => item.id === clip?.sequenceRef);
    if (!sequence) break;
    trail.push({ name: sequence.name || id, sequence });
  }
  const current = sequence && trail.length === path.length + 1 ? sequence : undefined;
  const selectedTrack = current?.tracks.find(track => track.clips.some(clip => clip.id === selected));
  const clip = selectedTrack?.clips.find(clip => clip.id === selected);
  const end = Math.max(.1, ...current?.tracks.flatMap(track => track.clips.map(clip => rationalToSecs(clip.timelineEnd))) || []);
  const disabled = busy || !!state.editLock || !!selectedTrack?.locked;
  useEffect(() => {
    const open = async (event: Event) => {
      const id = (event as CustomEvent<{ clipId: string }>).detail.clipId;
      if (!await flushPendingEdits()) return;
      const latest = getLatestState();
      if (!latest?.currentId) return;
      positions.current.clear(); setOwner(latest.currentId); setPath([id]); setTime(0); setSelected(null); setError("");
    };
    window.addEventListener("cutvoke:composition-open", open);
    return () => window.removeEventListener("cutvoke:composition-open", open);
  }, []);
  useEffect(() => {
    if (!owner) return;
    const focus = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { dialog.current?.close(); if (focus?.isConnected) focus.focus(); };
  }, [owner]);
  useEffect(() => { if (owner && (owner !== state.currentId || !current)) setOwner(null); }, [owner, state.currentId, !!current]);
  const pathKey = JSON.stringify(path);
  useEffect(() => {
    if (!owner || !current) return;
    const abort = new AbortController(); let url = "";
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`/api/v1/projects/${encodeURIComponent(owner)}/interactive-frame`, { method: "POST", signal: abort.signal,
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ compositionPath: path, bakedTitles: true, t: Math.min(time, end - .001), maximum: 960, client: client.current, serial: ++serial.current }) });
        if (!response.ok) { const result = await response.json(); throw new Error(result.error?.message || response.statusText); }
        if (response.status === 204) { setFrame(""); return; }
        url = URL.createObjectURL(await response.blob());
        if (!abort.signal.aborted) { setFrame(url); setError(""); }
      } catch (failure) { if (!abort.signal.aborted) setError(String(failure)); }
    }, 180);
    return () => { clearTimeout(timer); abort.abort(); if (url) URL.revokeObjectURL(url); };
  }, [owner, pathKey, state.revision, time]);
  async function edit(type: string, payload: Record<string, unknown>) {
    setBusy(true);
    try { await runCommand(dispatch, getLatestState() || state, "composition.edit", { path, command: { type, payload } }); }
    finally { setBusy(false); }
  }
  function go(next: string[]) {
    positions.current.set(pathKey, time); setPath(next); setTime(positions.current.get(JSON.stringify(next)) || 0); setSelected(null); setFrame("");
  }
  if (!owner) return null;
  const text = clip?.effects?.find(effect => effect.effectId === "cutvoke.text");
  return <dialog ref={dialog} className="composition-dialog" aria-label={tr("预合成编辑")} onCancel={event => { event.preventDefault(); setOwner(null); }}>
    <header><strong>{tr("预合成编辑")}</strong><button type="button" className="cv-btn" autoFocus onClick={() => setOwner(null)}>{tr("关闭")}</button></header>
    <nav aria-label={tr("合成路径")}>{trail.map((item, index) => <button type="button" className="cv-btn cv-btn--sm" key={index} disabled={index === trail.length - 1}
      onClick={() => index ? go(path.slice(0, index)) : setOwner(null)}>{item.name}</button>)}</nav>
    <p className="cv-hint">{tr("这里编辑母版，时间以当前合成为零点；实例覆盖可在主时间线修改。")}</p>
    <div className="composition-dialog__body"><div>
      <div className="composition-dialog__preview">{frame ? <img src={frame} alt={tr("预合成预览")} /> : <span>{tr("正在准备预览…")}</span>}</div>
      <label>{tr("局部时间")}<input aria-label={tr("局部时间")} type="range" min={0} max={end} step={1 / (current ? rationalToSecs(current.fps) : 30)} value={time} onChange={event => setTime(Number(event.target.value))} /> {time.toFixed(2)} s</label>
      {current?.tracks.map(track => <div className="composition-lane" key={track.id}><span>{track.kind} · {track.id}</span><div className="composition-lane__clips">
        {track.clips.map(item => <button type="button" className="cv-btn cv-btn--sm" key={item.id} aria-pressed={selected === item.id}
          style={{ left: `${rationalToSecs(item.timelineStart) / end * 100}%`, width: `${(rationalToSecs(item.timelineEnd) - rationalToSecs(item.timelineStart)) / end * 100}%` }}
          onClick={() => { setSelected(item.id); setTime(rationalToSecs(item.timelineStart)); }} onDoubleClick={() => { if (item.nested || item.sequenceRef) go([...path, item.id]); }}>
          {item.nested?.name || item.sequenceRef || assetDisplayName(item.assetRef) || item.id}</button>)}
      </div></div>)}
    </div><aside>
      {clip ? <fieldset disabled={disabled} style={{ border: 0, padding: 0, minWidth: 0 }}><strong>{assetDisplayName(clip.assetRef) || clip.id}</strong>
        {[['timelineStart', tr("开始")], ['timelineEnd', tr("结束")]].map(([key, label]) => <label key={key}>{label}<input type="number" className="cv-input" min={0} step={.1}
          key={`${state.revision}:${clip.id}:${key}`} defaultValue={rationalToSecs(clip[key as "timelineStart" | "timelineEnd"])}
          onBlur={event => { const value = Number(event.target.value); if (event.target.validity.valid && value !== rationalToSecs(clip[key as "timelineStart" | "timelineEnd"]))
            void edit("clip.trim", { clipId: clip.id, [key]: secsToRational(value) }); }} /></label>)}
        {text ? <label>{tr("文字")}<textarea className="cv-input" key={`${state.revision}:${clip.id}:text`} defaultValue={String(text.params.content || "")}
          onBlur={event => { if (event.target.value !== text.params.content) void edit("effect.update", { clipId: clip.id, instanceId: text.instanceId, effectId: "cutvoke.text", params: { content: event.target.value } }); }} /></label> : null}
        {clip.nested || clip.sequenceRef ? <button type="button" className="cv-btn" onClick={() => go([...path, clip.id])}>{tr("进入预合成")}</button> : null}
        <button type="button" className="cv-btn cv-btn--sm" onClick={() => void edit("effect.add", { clipId: clip.id, effectId: "cutvoke.fx.blur", params: { sigma: 5 } })}>{tr("添加模糊")}</button>
      </fieldset> : <p>{tr("选择片段以编辑")}</p>}
      <details><summary>{tr("依赖图")}</summary>{trail.map((item, index) => <p key={index} style={{ paddingInlineStart: index * 12 }}>{item.name}</p>)}</details>
    </aside></div>
    {error ? <p role="alert">{error}</p> : null}
  </dialog>;
}
