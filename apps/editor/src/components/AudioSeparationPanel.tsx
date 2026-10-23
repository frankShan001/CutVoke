import { useMemo, useState } from "react";
import { tr } from "../i18n";
import { useEditor, showError } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { sendCommand, newCommandId } from "../lib/api";
import { assetDisplayName, useSessionAssets } from "../lib/assetStore";
import { secsToRational } from "../lib/rational";
import { Button, Field, Panel, Select, TextInput } from "./ui";

type Stem = { path: string; duration: number };
export function AudioSeparationPanel() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const assets = useSessionAssets();
  const [path, setPath] = useState(""), [outDir, setOutDir] = useState("");
  const [model, setModel] = useState("htdemucs"), [stems, setStems] = useState("vocals");
  const [busy, setBusy] = useState(false), [result, setResult] = useState<Record<string, Stem> | null>(null);
  const sources = useMemo(() => {
    const paths = new Set(assets.filter(asset => ["audio", "video"].includes(asset.kind)).map(asset => asset.path));
    for (const track of state.project?.sequence.tracks || []) if (["audio", "video"].includes(track.kind))
      for (const clip of track.clips) paths.add(clip.assetRef.sourcePath);
    return [...paths];
  }, [assets, state.project]);
  const separate = async () => {
    setBusy(true); setResult(null);
    try {
      const latest = getLatestState() || state;
      if (!latest.currentId) return;
      const response = await sendCommand(latest.currentId, "audio.separate", { audioPath: path, outDir, model, stems, device: "auto" }, latest.revision, newCommandId());
      const receipt = response.changedEntities.find(entry => entry.type === "audio_separation") as unknown as { stems?: Record<string, Stem> };
      if (receipt?.stems) setResult(receipt.stems);
    } catch (error) { showError(dispatch, error);
    } finally { setBusy(false); }
  };
  const insert = async (stem: Stem) => {
    const latest = getLatestState() || state;
    await runCommand(dispatch, latest, "clip.insert", { trackId: `stem_${crypto.randomUUID()}`, createTrackKind: "audio", sourcePath: stem.path,
      sourceStart: secsToRational(0), timelineStart: secsToRational(latest.playhead.toFixed(6)), timelineEnd: secsToRational((latest.playhead + stem.duration).toFixed(6)) });
  };
  return <Panel title={tr("音源分离")} subtitle={tr("本地 Demucs 模型分离人声与伴奏；首次使用需要下载模型。") }>
    <Field label={tr("音频源")}><Select value={path} disabled={busy} onChange={event => {
      setPath(event.target.value); setOutDir(event.target.value.replace(/[^\\/]+$/, `separated_${Date.now()}`));
    }}><option value="">{tr("选择已导入音频…")}</option>{sources.map(source => <option key={source} value={source}>{assetDisplayName(source)}</option>)}</Select></Field>
    <Field label={tr("音频路径")}><TextInput value={path} disabled={busy} onChange={e => setPath(e.target.value)} /></Field>
    <Field label={tr("输出文件夹")}><TextInput value={outDir} disabled={busy} onChange={e => setOutDir(e.target.value)} /></Field>
    <Field label={tr("模型")}><Select value={model} disabled={busy} onChange={e => setModel(e.target.value)}><option>htdemucs</option><option>htdemucs_ft</option></Select></Field>
    <Field label={tr("输出音轨")}><Select value={stems} disabled={busy} onChange={e => setStems(e.target.value)}>
      <option value="vocals">{tr("人声与伴奏")}</option><option value="all">{tr("人声、鼓、贝斯、其他")}</option>
    </Select></Field>
    <Button full variant="primary" disabled={busy || !state.currentId || !path.trim() || !outDir.trim()} onClick={() => void separate()}>{busy ? tr("正在本地分离…") : tr("开始分离")}</Button>
    {result ? Object.entries(result).map(([name, stem]) => <div key={name}><strong>{name}</strong><p className="cv-hint">{stem.path}</p><Button disabled={!!state.editLock} onClick={() => void insert(stem)}>{tr("添加到时间线")}</Button></div>) : null}
  </Panel>;
}
