import { useState } from "react";
import { tr } from "../i18n";
import { useEditor } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { rationalToSecs } from "../lib/rational";
import type { Sequence } from "../types/api";

export function CompositionLibrary() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [sequenceId, setSequenceId] = useState(""), [busy, setBusy] = useState(false);
  const project = state.project;
  if (!project) return null;
  const choices = project.sequences?.filter(item => item.id !== project.sequence.id) || [];
  const edges: { parent: string; clip: string; source: string; path?: string[] }[] = [];
  function walk(sequence: Sequence, path: string[], active: boolean, depth = 0) {
    if (depth > 16 || edges.length > 2048) return;
    for (const track of sequence.tracks) for (const clip of track.clips) {
      if (clip.sequenceRef || clip.nested) {
        edges.push({ parent: sequence.name || sequence.id, clip: clip.id, source: clip.nested?.name || clip.sequenceRef || clip.nested!.id, path: active ? [...path, clip.id] : undefined });
        if (clip.nested) walk(clip.nested, [...path, clip.id], active, depth+1);
      }
    }
  }
  for (const sequence of project.sequences || [project.sequence]) walk(sequence, [], sequence.id === project.sequence.id);
  async function insert() {
    const current = getLatestState() || state;
    const master = current.project?.sequences?.find(item => item.id === sequenceId);
    if (!master || current.currentId !== state.currentId) return;
    setBusy(true);
    const duration = Math.max(.1, ...master.tracks.flatMap(track => track.clips.map(clip => rationalToSecs(clip.timelineEnd))));
    try { await runCommand(dispatch, current, "composition.instance", { sequenceId, timelineStart: current.playhead, timelineEnd: current.playhead + duration }); }
    finally { setBusy(false); }
  }
  return <details className="composition-library"><summary>{tr("预合成与依赖")}</summary>
    <select className="cv-input" aria-label={tr("共享母版")} value={sequenceId} onChange={event => setSequenceId(event.target.value)}>
      <option value="">{tr("选择序列")}</option>{choices.map(item => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
    </select>
    <button type="button" className="cv-btn cv-btn--sm" disabled={busy || !!state.editLock || !choices.some(item => item.id === sequenceId)} onClick={() => void insert()}>{tr("插入共享实例")}</button>
    <div style={{ maxHeight: 240, overflow: "auto" }}><table style={{ width: "100%" }}><thead><tr><th>{tr("母版")}</th><th>{tr("实例")}</th><th>{tr("来源")}</th></tr></thead>
      <tbody>{edges.map((item,i) => <tr key={i}><td>{item.parent}</td><td><code>{item.clip}</code></td><td>{item.source}</td></tr>)}</tbody></table></div>
  </details>;
}
