import { useState } from "react";
import { tr, useLocale } from "../../i18n";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import type { Clip, Sequence } from "../../types/api";

function defaultValue(sequence: Sequence, control: NonNullable<Sequence["exposedProperties"]>[number]) {
  const child = sequence.tracks.flatMap(track => track.clips).find(item => item.id === control.clipId);
  if (control.param === "content") return String(child?.effects?.find(item => item.effectId === "cutvoke.text")?.params.content || "");
  if (control.param === "volume") return child?.volume ? Number(child.volume.num) / Number(child.volume.den) : 1;
  if (control.param.startsWith("effect:")) {
    const [, instance, key] = control.param.split(":");
    return Number(child?.effects?.find(item => item.instanceId === instance)?.params[key] || 0);
  }
  const transform = child?.effects?.find(item => item.effectId === "cutvoke.transform")?.params || {};
  return Number(["x", "y"].includes(control.param) ? (transform.position as Record<string, unknown> | undefined)?.[control.param] || 0 :
    transform[control.param] ?? (["opacity", "scale"].includes(control.param) ? 1 : 0));
}

export function EssentialPropertiesSection({ clip }: { clip: Clip }) {
  useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [parameter, setParameter] = useState("opacity"), [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const child = clip.nested || state.project?.sequences?.find(seq => seq.id === clip.sequenceRef);
  const controls = child?.exposedProperties || [];
  async function run(kind: string, payload: Record<string, unknown>) {
    setBusy(true);
    try { return await runCommand(dispatch, getLatestState() || state, kind, payload); }
    finally { setBusy(false); }
  }
  return <details className="inspector__section" data-testid="essential-properties"><summary>{tr("模板参数与预合成")}</summary>
    <fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0 }}>
      {child ? <>
        <button type="button" className="cv-btn cv-btn--sm" onClick={() => window.dispatchEvent(new CustomEvent("cutvoke:composition-open", { detail: { clipId: clip.id } }))}>{tr("进入预合成")}</button>
        {controls.map(control => {
          const value = clip.propertyOverrides?.[control.id] ?? defaultValue(child, control);
          return <label className="inspector__subrow" key={`${clip.id}:${control.id}`}><span>{control.label || control.id}</span>
            <input className="cv-input" aria-label={control.label || control.id} type={typeof value === "number" ? "number" : "text"}
              defaultValue={value} key={`${state.revision}:${control.id}`} step={typeof value === "number" ? .05 : undefined}
              onBlur={event => { const next = typeof value === "number" ? Number(event.target.value) : event.target.value;
                if (next !== value) void run("composition.override", { clipId: clip.id, values: { ...clip.propertyOverrides, [control.id]: next } }); }} /></label>;
        })}
        {Object.keys(clip.propertyOverrides || {}).length ? <button type="button" className="cv-btn cv-btn--sm" onClick={() => void run("composition.override", { clipId: clip.id, values: {} })}>{tr("恢复母版参数")}</button> : null}
      </> : null}
      <label className="inspector__subrow"><span>{tr("公开属性")}</span><select className="cv-input" value={parameter} onChange={event => setParameter(event.target.value)}>
        <option value="opacity">{tr("透明度")}</option><option value="x">X</option><option value="y">Y</option>
        <option value="scale">{tr("缩放")}</option><option value="rotation">{tr("旋转")}</option><option value="volume">{tr("音量")}</option>
        {clip.effects?.some(item => item.effectId === "cutvoke.text") ? <option value="content">{tr("文字")}</option> : null}
      </select></label>
      <input className="cv-input" aria-label={tr("参数名称")} placeholder={tr("参数名称")} value={label} maxLength={128} onChange={event => setLabel(event.target.value)} />
      <button type="button" className="cv-btn cv-btn--sm" onClick={() => {
        const id = `${clip.id}.${parameter}`.replace(/[^a-zA-Z0-9_.-]/g, "_").slice(0, 96);
        const controls = (state.project?.sequence.exposedProperties || []).filter(item => item.id !== id);
        void run("composition.expose", { controls: [...controls, { id, label: label || parameter, clipId: clip.id, param: parameter }] });
      }}>{tr("公开为模板参数")}</button>
    </fieldset>
  </details>;
}
