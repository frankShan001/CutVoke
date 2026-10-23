import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import { flushPendingEdits } from "../../lib/pendingEdits";
import { rationalToSecs } from "../../lib/rational";
import { assetDisplayName } from "../../lib/assetStore";
import { tr } from "../../i18n";
import { selectedClipIds } from "../../store/timelineSelection";

export function TimelineEditTools() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("1");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const details = useRef<HTMLDetailsElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const close = () => {
    if (details.current) details.current.open = false;
    setOpen(false);
    details.current?.querySelector("summary")?.focus();
  };
  useEffect(() => { if (open) dialog.current?.focus(); }, [open]);
  const tracks = state.project?.sequence.tracks || [];
  const clips = tracks.flatMap(track => track.clips.map(clip => ({ track, clip })));
  useEffect(() => { setPicked([]); }, [state.currentId, state.project?.sequence.id]);
  const validRange = Number.isFinite(Number(start)) && Number.isFinite(Number(end))
    && Number(start) >= 0 && Number(end) > Number(start);
  const run = async (command: string, payload: Record<string, unknown>) => {
    setBusy(true);
    try {
      if (!await flushPendingEdits()) return;
      const result = await runCommand(dispatch, getLatestState() || state, command, payload);
      if (result.ok) {
        setPicked([]);
        dispatch({ type: "STATUS_SET", severity: "ok", text: tr("已完成时间线编辑，可一次撤销") });
        dispatch({ type: "SELECTION_SET", selection: null });
      }
    } finally { setBusy(false); }
  };
  return <details ref={details} className="timeline-edit-tools" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary className="cv-btn cv-btn--sm cv-btn--secondary">{tr("联动与复合")}</summary>
    {open && createPortal(<div ref={dialog} className="timeline-edit-tools__body" role="dialog" aria-label={tr("联动与复合")} tabIndex={-1}
      onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); close(); } }}>
      <button className="cv-btn cv-btn--sm" style={{ justifySelf: "end" }} onClick={close}>{tr("关闭")}</button>
      <strong>{tr("跨轨联动删除")}</strong>
      <div className="timeline-edit-tools__range">
        <label>{tr("开始 (s)")}<input className="cv-input" type="number" min="0" step="any" value={start}
          onChange={event => setStart(event.target.value)} /></label>
        <button className="cv-btn cv-btn--sm" onClick={() => setStart(String(getLatestState()?.playhead || 0))}>{tr("设为入点")}</button>
        <label>{tr("结束 (s)")}<input className="cv-input" type="number" min="0" step="any" value={end}
          onChange={event => setEnd(event.target.value)} /></label>
        <button className="cv-btn cv-btn--sm" onClick={() => setEnd(String(getLatestState()?.playhead || 0))}>{tr("设为出点")}</button>
      </div>
      <p className="cv-hint">{tr("所有轨道、字幕和标记同步收拢；受影响轨道须先解锁。")}</p>
      <button className="cv-btn cv-btn--sm cv-btn--secondary" disabled={!validRange || busy || !!state.editLock}
        onClick={() => void run("sequence.deleteRange", { start: Number(start), end: Number(end) })}>{tr("删除区间并联动收拢")}</button>
      <hr />
      <strong>{tr("跨轨复合片段")}</strong>
      <button className="cv-btn cv-btn--sm" disabled={!selectedClipIds(state).length}
        onClick={()=>setPicked(selectedClipIds(state))}>{tr("使用时间线所选片段")}</button>
      <button className="cv-btn cv-btn--sm" onClick={() => {
        const time = getLatestState()?.playhead || 0;
        setPicked(clips.filter(({clip,track}) => !track.locked && rationalToSecs(clip.timelineStart) <= time
          && time < rationalToSecs(clip.timelineEnd)).map(({clip}) => clip.id));
      }}>{tr("选择播放头处图层")}</button>
      <button className="cv-btn cv-btn--sm" disabled={!picked.length} onClick={() => {
        const all = new Set(picked);
        let changed = true;
        while (changed) {
          changed = false;
          for (const { clip } of clips) {
            if (clip.attachedToClipId && (all.has(clip.id) || all.has(clip.attachedToClipId))) {
              if (!all.has(clip.id) || !all.has(clip.attachedToClipId)) changed = true;
              all.add(clip.id); all.add(clip.attachedToClipId);
            }
          }
        }
        setPicked([...all]);
      }}>{tr("包含绑定图层")}</button>
      <div className="timeline-edit-tools__clips">
        {clips.map(({track,clip}) => <label key={clip.id}>
          <input type="checkbox" checked={picked.includes(clip.id)} disabled={track.locked || busy}
            onChange={event => setPicked(event.target.checked ? [...picked, clip.id] : picked.filter(id => id !== clip.id))} />
          <span>{clip.nested ? tr("复合片段") : assetDisplayName(clip.assetRef)} · {track.kind} · {rationalToSecs(clip.timelineStart).toFixed(2)}–{rationalToSecs(clip.timelineEnd).toFixed(2)}s</span>
        </label>)}
      </div>
      <button className="cv-btn cv-btn--sm cv-btn--secondary" disabled={!picked.length || busy || !!state.editLock}
        onClick={() => void run("clip.group", { clipIds: picked })}>{tr("创建复合片段")}</button>
    </div>, document.body)}
  </details>;
}
