import { useState } from "react";
import { tr, useLocale } from "../../i18n";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import { assetDisplayName } from "../../lib/assetStore";
import type { Clip } from "../../types/api";

export function CompositingSection({ clip }: { clip: Clip }) {
  useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [busy, setBusy] = useState(false);
  const value = clip.compositing || {};
  const choices = state.project?.sequence.tracks.filter(track => ["video", "text"].includes(track.kind))
    .flatMap(track => track.clips).filter(item => item.id !== clip.id && item.role !== "adjustment") || [];
  async function update(patch: Partial<NonNullable<Clip["compositing"]>>) {
    setBusy(true);
    try { await runCommand(dispatch, getLatestState() || state, "clip.composite", { clipId: clip.id, compositing: { ...value, ...patch } }); }
    finally { setBusy(false); }
  }
  if (clip.role === "adjustment") return <p className="cv-hint">{tr("调整层影响下方图层，作用时间由片段区间决定。")}</p>;
  return <details className="inspector__section" data-testid="layer-compositing">
    <summary>{tr("图层合成")}</summary>
    <fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0 }}>
      <label className="inspector__subrow"><span>{tr("轨道遮罩")}</span>
        <select className="cv-input" aria-label={tr("轨道遮罩")} value={value.matte?.clipId || ""}
          onChange={event => void update({ matte: event.target.value ? { ...value.matte, clipId: event.target.value } : null })}>
          <option value="">{tr("无")}</option>
          {choices.map(item => <option key={item.id} value={item.id}>{assetDisplayName(item.assetRef) || item.id} · {item.id}</option>)}
        </select>
      </label>
      {value.matte ? <>
        <label className="inspector__subrow"><span>{tr("遮罩通道")}</span><select className="cv-input" value={value.matte.channel || "alpha"}
          aria-label={tr("遮罩通道")} onChange={event => void update({ matte: { ...value.matte!, channel: event.target.value as "alpha" | "luma" } })}>
          <option value="alpha">Alpha</option><option value="luma">{tr("亮度")}</option></select></label>
        <label className="inspector__subrow"><input type="checkbox" checked={!!value.matte.invert}
          onChange={event => void update({ matte: { ...value.matte!, invert: event.target.checked } })} />{tr("反相")}</label>
      </> : null}
      <label className="inspector__subrow"><input type="checkbox" checked={!!value.matteOnly}
        onChange={event => void update({ matteOnly: event.target.checked })} />{tr("仅用作遮罩")}</label>
    </fieldset>
  </details>;
}
