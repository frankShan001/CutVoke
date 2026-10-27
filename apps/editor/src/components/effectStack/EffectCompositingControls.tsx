import { useEffect, useState } from "react";
import { tr, useLocale } from "../../i18n";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import type { EffectCompositing, EffectInstance } from "../../types/api";

export function EffectCompositingControls({ effect, disabled }: { effect: EffectInstance; disabled: boolean }) {
  useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [draft, setDraft] = useState<EffectCompositing>(effect.compositing || {});
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(effect.compositing || {}), [effect]);
  if (!effect.instanceId || ["cutvoke.transform", "cutvoke.text"].includes(effect.effectId) || effect.effectId.startsWith("cutvoke.transition.")) return null;
  async function commit(value: EffectCompositing) {
    if (JSON.stringify(value) === JSON.stringify(effect.compositing || {})) return;
    setBusy(true);
    try { await runCommand(dispatch, getLatestState() || state, "effect.composite", { clipId: state.selection?.clipId, instanceId: effect.instanceId, compositing: value }); }
    finally { setBusy(false); }
  }
  return <details className="effect-row__compositing"><summary>{tr("效果混合")}</summary>
    <fieldset disabled={disabled || busy} style={{ border: 0, padding: 0, minWidth: 0 }}>
      <label className="inspector__subrow"><span>{tr("透明度")}</span><input className="cv-input" type="number" min={0} max={1} step={.05}
        aria-label={tr("效果透明度")} value={draft.opacity ?? 1} onChange={event => setDraft({ ...draft, opacity: Number(event.target.value) })}
        onBlur={() => void commit(draft)} /></label>
      <label className="inspector__subrow"><span>{tr("混合模式")}</span><select className="cv-input" aria-label={tr("效果混合模式")} value={draft.blendMode || "normal"}
        onChange={event => { const value = { ...draft, blendMode: event.target.value as EffectCompositing["blendMode"] }; setDraft(value); void commit(value); }}>
        <option value="normal">{tr("正常")}</option><option value="multiply">{tr("正片叠底")}</option><option value="screen">{tr("滤色")}</option>
        <option value="add">{tr("相加")}</option><option value="difference">{tr("差值")}</option></select></label>
      <label className="inspector__subrow"><input type="checkbox" checked={!!draft.region}
        onChange={event => { const value = { ...draft, region: event.target.checked ? { x: 0, y: 0, width: 1, height: 1, feather: 0 } : null }; setDraft(value); void commit(value); }} />{tr("局部范围")}</label>
      {draft.region ? (Object.entries({ x: tr("横向位置"), y: tr("纵向位置"), width: tr("宽度"), height: tr("高度"), feather: tr("羽化") }) as ["x" | "y" | "width" | "height" | "feather", string][]).map(([key, label]) =>
        <label className="inspector__subrow" key={key}><span>{label}</span><input className="cv-input" aria-label={label} type="number" min={key === "width" || key === "height" ? .001 : 0} max={1} step={.01}
          value={draft.region?.[key] ?? 0} onChange={event => setDraft({ ...draft, region: { ...draft.region, [key]: Number(event.target.value) } })}
          onBlur={event => { if (event.currentTarget.validity.valid) void commit(draft); }} /></label>) : null}
    </fieldset>
  </details>;
}
