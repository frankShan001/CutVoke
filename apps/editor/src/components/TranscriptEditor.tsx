import { useEffect, useState } from "react";
import { useEditor } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { flushPendingEdits } from "../lib/pendingEdits";
import { rationalToSecs } from "../lib/rational";
import { tr } from "../i18n";

export function TranscriptEditor() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [selected, setSelected] = useState<string[]>([]);
  const [wordCaption, setWordCaption] = useState("");
  const [words, setWords] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const captions = state.project?.sequence.captions || [];
  const caption = captions.find(item => item.id === wordCaption);
  useEffect(() => { setSelected([]); setWordCaption(""); setWords([]); }, [state.currentId, state.project?.sequence.id]);
  const remove = async (payload: Record<string, unknown>) => {
    setBusy(true);
    try {
      if (!await flushPendingEdits()) return;
      const result = await runCommand(dispatch, getLatestState() || state, "transcript.delete", payload);
      if (result.ok) {
        setSelected([]); setWords([]);
        dispatch({ type: "STATUS_SET", severity: "ok", text: tr("已删去对应画面与声音，字幕同步收拢，可一次撤销") });
      }
    } finally { setBusy(false); }
  };
  if (!captions.length) return null;
  return <details style={{ margin: "12px 0" }}>
    <summary className="cv-btn cv-btn--secondary cv-btn--sm">{tr("按文本剪视频")}</summary>
    <p className="cv-hint">{tr("选择句子或词，删除对应时间内的画面和声音，所有轨道联动收拢。")}</p>
    <div style={{ maxHeight: 180, overflow: "auto", display: "grid", gap: 8, marginBottom: 8 }}>
      {captions.map(item => <label key={item.id} style={{ display: "flex", gap: 8, alignItems: "start" }}>
        <input type="checkbox" disabled={busy || !!state.editLock} checked={selected.includes(item.id)}
          onChange={event => setSelected(event.target.checked ? [...selected, item.id] : selected.filter(id => id !== item.id))} />
        <span><small>{rationalToSecs(item.start).toFixed(2)}s</small> {item.text}</span>
      </label>)}
    </div>
    <button className="cv-btn cv-btn--secondary cv-btn--sm" disabled={!selected.length || busy || !!state.editLock}
      onClick={() => void remove({ captionIds: selected })}>{tr("删除所选句子及画面")}</button>
    <label style={{ display: "grid", gap: 6, marginTop: 12 }}>{tr("按词剪辑")}
      <select className="cv-input" value={wordCaption} onChange={event => { setWordCaption(event.target.value); setWords([]); }}>
        <option value="">{tr("选择有词级时间的句子")}</option>
        {captions.filter(item => item.words?.length).map(item => <option key={item.id} value={item.id}>{item.text}</option>)}
      </select>
    </label>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "8px 0" }}>
      {caption?.words?.map((word, index) => <button key={index} className="cv-btn cv-btn--sm"
        aria-pressed={words.includes(index)} style={words.includes(index) ? { outline: "2px solid var(--accent,#56c5b6)" } : undefined}
        onClick={() => setWords(words.includes(index) ? words.filter(i => i !== index) : [...words, index])}>{word.text}</button>)}
    </div>
    <button className="cv-btn cv-btn--secondary cv-btn--sm" disabled={!words.length || busy || !!state.editLock}
      onClick={() => void remove({ captionId: wordCaption, wordIndices: words })}>{tr("删除所选词及画面")}</button>
  </details>;
}
