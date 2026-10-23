import { useEffect, useState } from "react";
import type { Clip } from "../../types/api";
import { tr, useLocale } from "../../i18n";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import { rationalToSecs } from "../../lib/rational";
import { clipSourceRange, parseTimingInput, timingPayload, type TimingField } from "../../lib/clipTiming";
import "./clip-timing.css";

export function ClipTimingSection({ clip, hasSource }: { clip: Clip; hasSource: boolean }) {
  useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [draft, setDraft] = useState<{ field: TimingField; value: string } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  useEffect(() => { setDraft(null); setError(""); }, [clip.id]);
  const sequence = state.project?.sequence;
  if (!sequence) return null;
  const start = rationalToSecs(clip.timelineStart), end = rationalToSecs(clip.timelineEnd);
  const source = clipSourceRange(clip);
  const fields: { field: TimingField; label: string; value: number }[] = [
    { field: "start", label: tr("时间线起点"), value: start },
    { field: "end", label: tr("时间线终点"), value: end },
    { field: "duration", label: tr("片段时长"), value: end - start },
    ...(hasSource ? [
      { field: "sourceIn" as const, label: tr("源入点"), value: source.start },
      { field: "sourceOut" as const, label: tr("源出点"), value: source.end },
    ] : []),
  ];
  async function commit() {
    if (!draft || busy || !sequence) return;
    try {
      const value = parseTimingInput(draft.value, sequence.fps);
      const payload = timingPayload(clip, draft.field, value, sequence.fps);
      if (Object.keys(payload).length === 1) { setDraft(null); setError(""); return; }
      setBusy(true); setError("");
      const result = await runCommand(dispatch, getLatestState() || state, "clip.trim", payload);
      if (result.ok) setDraft(null);
      else setError(result.error?.message || tr("修改时间失败，请检查片段边界与相邻片段。"));
    } catch {
      setError(tr("请输入秒、帧数（如 45f）或时码（如 00:00:01:15）；终点必须晚于起点，曲线变速仅可在现有范围内裁剪。"));
    } finally { setBusy(false); }
  }
  return <section className="clip-timing" aria-label={tr("精确裁剪")}>
    <h4 className="inspector__section-title">{tr("精确裁剪")}</h4>
    <p className="cv-hint">{tr("输入秒、帧数或时码，按工程帧率对齐；修改起点会裁剪左端。")}</p>
    {fields.map(({ field, label, value }) => <label key={field} className="inspector__subrow clip-timing__row">
      <span className="inspector__key">{label}</span>
      <input className="cv-input cv-mono" aria-label={label} type="text" inputMode="decimal"
        disabled={busy || (!!clip.freezeAt && field.startsWith("source"))}
        value={draft?.field === field ? draft.value : Number(value.toFixed(6)).toString()}
        onChange={event => { setDraft({ field, value: event.target.value }); setError(""); }}
        onKeyDown={event => {
          if (event.key === "Enter") { event.preventDefault(); void commit(); }
          if (event.key === "Escape") { event.preventDefault(); setDraft(null); setError(""); }
        }} />
      <span className="clip-timing__unit">{tr("秒")}</span>
    </label>)}
    {hasSource && source.end < source.start ? <p className="cv-hint">{tr("倒放片段的源出点早于源入点。")}</p> : null}
    {clip.freezeAt ? <p className="cv-hint">{tr("定格片段使用固定源帧。")}</p> : null}
    <div className="clip-timing__actions"><button className="cv-btn cv-btn--sm cv-btn--primary" disabled={!draft || busy}
      onClick={() => void commit()}>{busy ? tr("保存中…") : tr("应用裁剪")}</button>
      {draft ? <button className="cv-btn cv-btn--sm" disabled={busy} onClick={() => { setDraft(null); setError(""); }}>{tr("取消")}</button> : null}
    </div>
    {error ? <p className="cv-hint clip-timing__error" role="alert">{error}</p> : null}
  </section>;
}
