import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useEditor } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import type { AudioMix } from "../types/api";
import { Button, Field, Panel, Select, TextInput } from "./ui";

const effectOptions = [
  { id: "cutvoke.fx.equalizer", label: "EQ", params: { frequency: 1000, width: 1, gainDb: 0 } },
  { id: "cutvoke.fx.compressor", label: "Compressor", params: { threshold: -20, ratio: 4, attack: 20, release: 250 } },
  { id: "cutvoke.fx.loudnorm", label: "LUFS", params: { I: -16, TP: -1.5 } },
] as const;

export function AudioMixerPanel() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [target, setTarget] = useState("master"), [draft, setDraft] = useState<AudioMix>({}), [busy, setBusy] = useState(false);
  const [time, setTime] = useState(0), [pointGain, setPointGain] = useState(0), [effect, setEffect] = useState(effectOptions[0].id as string);
  const sequence = state.project?.sequence;
  const track = sequence?.tracks.find(track => `track:${track.id}` === target);
  const bus = sequence?.audioBuses?.find(bus => `bus:${bus.id}` === target);
  const current = target === "master" ? sequence?.masterAudio : track?.audioMix || bus?.mix;
  useEffect(() => setDraft(structuredClone(current || {})), [target, state.project?.revision]);
  const targetPayload = target === "master" ? { master: true } : track ? { trackId: track.id } : { busId: bus?.id };
  const apply = async () => {
    setBusy(true);
    try { await runCommand(dispatch, getLatestState() || state, "audio.mixer", { ...targetPayload, mix: draft }); }
    finally { setBusy(false); }
  };
  const envelope = draft.envelope || [], effects = draft.effects || [];
  const locked = busy || !!state.editLock || !!track?.locked || !state.currentId;
  return <Panel title={tr("混音台")} subtitle={tr("轨道 → 子混音总线 → 主输出；包络使用工程时间。") }>
    <Field label={tr("混音通道")}><Select value={target} disabled={busy} onChange={event => setTarget(event.target.value)} aria-label={tr("混音通道")}>
      <option value="master">{tr("主输出")}</option>
      {sequence?.tracks.filter(t => ["audio", "video"].includes(t.kind) && t.role !== "sticker").map(t => <option key={t.id} value={`track:${t.id}`}>{t.id}</option>)}
      {sequence?.audioBuses?.map(b => <option key={b.id} value={`bus:${b.id}`}>{b.name}</option>)}
    </Select></Field>
    <Field label={tr("增益（dB）")}><TextInput type="number" min={-96} max={24} step={.5} value={draft.gainDb ?? 0} disabled={locked} onChange={e => setDraft({ ...draft, gainDb: Number(e.target.value) })} /></Field>
    <Field label={tr("声像（左 -1，右 1）")}><TextInput type="number" min={-1} max={1} step={.05} value={draft.pan ?? 0} disabled={locked} onChange={e => setDraft({ ...draft, pan: Number(e.target.value) })} /></Field>
    <label className="cv-row"><input type="checkbox" checked={draft.muted || false} disabled={locked} onChange={e => setDraft({ ...draft, muted: e.target.checked })} />{tr("静音")}</label>
    {target !== "master" ? <label className="cv-row"><input type="checkbox" checked={draft.solo || false} disabled={locked} onChange={e => setDraft({ ...draft, solo: e.target.checked })} />{tr("独奏")}</label> : null}
    {track ? <Field label={tr("输出到")}><Select value={draft.busId || ""} disabled={locked} onChange={e => setDraft({ ...draft, busId: e.target.value })}>
      <option value="">{tr("主输出")}</option>{sequence?.audioBuses?.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
    </Select></Field> : null}
    <details><summary>{tr("音量包络")}</summary>
      <Field label={tr("时间（秒）")}><TextInput type="number" min={0} step={.01} value={time} onChange={e => setTime(Number(e.target.value))} disabled={locked} /></Field>
      <Field label={tr("增益（dB）")}><TextInput type="number" min={-96} max={24} step={.5} value={pointGain} onChange={e => setPointGain(Number(e.target.value))} disabled={locked} /></Field>
      <Button disabled={locked} onClick={() => setTime((getLatestState() || state).playhead)}>{tr("使用播放头时间")}</Button>
      <Button disabled={locked} onClick={() => setDraft({ ...draft, envelope: [...envelope.filter(point => point.time !== time), { time, gainDb: pointGain }].sort((a, b) => a.time - b.time) })}>{tr("添加包络点")}</Button>
      {envelope.map((point, index) => <div className="cv-row" key={index}><span>{point.time.toFixed(2)} s · {point.gainDb} dB</span><Button disabled={locked} onClick={() => setDraft({ ...draft, envelope: envelope.filter((_, at) => at !== index) })}>{tr("删除")}</Button></div>)}
    </details>
    <details><summary>{tr("通道效果链")}</summary>
      <Select value={effect} disabled={locked} onChange={e => setEffect(e.target.value)}>{effectOptions.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</Select>
      <Button disabled={locked || effects.length >= 16} onClick={() => { const selected = effectOptions.find(option => option.id === effect)!; setDraft({ ...draft, effects: [...effects, { effectId: effect, params: { ...selected.params }, enabled: true }] }); }}>{tr("添加效果")}</Button>
      {effects.map((item, index) => <fieldset key={index} disabled={locked}><legend>{effectOptions.find(option => option.id === item.effectId)?.label || item.effectId}</legend>
        <label><input type="checkbox" checked={item.enabled !== false} onChange={e => setDraft({ ...draft, effects: effects.map((entry, at) => at === index ? { ...entry, enabled: e.target.checked } : entry) })} />{tr("启用")}</label>
        {Object.entries(item.params).filter(([, value]) => typeof value === "number").map(([key, value]) => <Field key={key} label={key}><TextInput type="number" step="any" value={value as number} onChange={e => setDraft({ ...draft, effects: effects.map((entry, at) => at === index ? { ...entry, params: { ...entry.params, [key]: Number(e.target.value) } } : entry) })} /></Field>)}
        <Button disabled={index === 0} onClick={() => { const next = [...effects]; [next[index-1], next[index]] = [next[index], next[index-1]]; setDraft({ ...draft, effects: next }); }}>{tr("上移")}</Button>
        <Button onClick={() => setDraft({ ...draft, effects: effects.filter((_, at) => at !== index) })}>{tr("删除")}</Button>
      </fieldset>)}
    </details>
    <Button variant="primary" full disabled={locked} onClick={() => void apply()}>{tr("应用混音")}</Button>
    <div className="cv-row"><Button disabled={locked} onClick={() => void runCommand(dispatch, getLatestState() || state, "audio.busAdd", { name: tr("子混音总线") })}>{tr("新建总线")}</Button>
      {bus ? <Button variant="danger" disabled={locked} onClick={async () => { const result = await runCommand(dispatch, getLatestState() || state, "audio.busRemove", { busId: bus.id }); if (result.ok) setTarget("master"); }}>{tr("删除总线")}</Button> : null}</div>
  </Panel>;
}
