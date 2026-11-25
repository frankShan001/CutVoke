import { BookmarkPlus } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { tr, useLocale } from "../../i18n";
import { getLatestState, runCommand } from "../../store/actions";
import { type EditorState, useEditor } from "../../store/editor";
import { rationalToSecs } from "../../lib/rational";
import { fmtTimePrecise, secsToFrameRatString } from "./util";

type MarkerDraft = {
  projectId: string;
  sequenceId: string;
  revision: string;
  time: { num: string; den: string };
};

function canWriteMarker(state: EditorState): boolean {
  return !!state.currentId && state.view === "editor" && state.serverUp &&
    !state.opening && !state.editLock && state.syncState !== "saving" &&
    state.project?.projectId === state.currentId && state.project.revision === state.revision;
}

function matchesDraft(state: EditorState, draft: MarkerDraft): boolean {
  return canWriteMarker(state) && state.currentId === draft.projectId &&
    state.project?.sequence.id === draft.sequenceId && state.revision === draft.revision;
}

export function TimelineMarkerTool({ end }: { end: number }) {
  useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [draft, setDraft] = useState<MarkerDraft | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [rejected, setRejected] = useState(false);
  const submitting = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const label = tr("添加审片标记");

  useEffect(() => {
    if (!draft) return;
    const element = dialog.current;
    if (!element) return;
    element.showModal();
    field.current?.focus();
    return () => { element.close(); trigger.current?.focus(); };
  }, [draft]);

  const open = () => {
    const latest = getLatestState() ?? state;
    const project = latest.project;
    if (!project || !canWriteMarker(latest) || latest.currentId !== state.currentId ||
        latest.revision !== state.revision || !Number.isFinite(end) || end <= 0 ||
        !Number.isFinite(latest.playhead) || latest.playhead < 0 || latest.playhead > end) return;
    const rate = rationalToSecs(project.sequence.fps);
    if (!Number.isFinite(rate) || rate <= 0) return;
    const frame = Math.min(Math.round(latest.playhead * rate), Math.floor(end * rate + 1e-9));
    if (!Number.isSafeInteger(frame)) return;
    setNote("");
    setRejected(false);
    setDraft({ projectId: project.projectId, sequenceId: project.sequence.id, revision: project.revision,
      time: secsToFrameRatString(frame / rate, project.sequence.fps) });
  };

  const submit = async () => {
    const name = note.trim();
    if (!draft || !name || name.length > 120 || /[\r\n]/.test(name) || submitting.current || rejected) return;
    const latest = getLatestState() ?? state;
    if (!matchesDraft(latest, draft)) {
      setRejected(true);
      return;
    }
    submitting.current = true;
    setBusy(true);
    try {
      const result = await runCommand(dispatch, latest, "marker.add", { name, time: draft.time }, { retryConflict: false });
      if (result.ok) setDraft(null);
      else setRejected(true);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  const stale = draft && (rejected || (!busy && !matchesDraft(state, draft)));
  return <>
    <button ref={trigger} type="button" className="timeline__zoom-btn timeline-marker-tool" disabled={!canWriteMarker(state) || !(end > 0)}
      aria-label={label} title={label} aria-haspopup="dialog" onClick={open}><BookmarkPlus size={14} aria-hidden="true" /></button>
    {draft && <dialog ref={dialog} className="timeline-marker-dialog" aria-labelledby={titleId}
      onCancel={event => { event.preventDefault(); if (!submitting.current) setDraft(null); }}>
      <form onSubmit={event => { event.preventDefault(); void submit(); }}>
        <strong id={titleId}>{label}</strong>
        <time>{fmtTimePrecise(rationalToSecs(draft.time))}</time>
        <label>{tr("标记备注")}
          <input ref={field} className="cv-input" type="text" value={note} maxLength={120} disabled={busy}
            onChange={event => setNote(event.currentTarget.value)} />
        </label>
        {stale ? <p role="status">{tr("工程已变化或暂不可编辑，请关闭后重新添加标记。")}</p> : null}
        <div className="timeline-marker-dialog__actions">
          <button className="cv-btn cv-btn--sm" type="button" disabled={busy} onClick={() => setDraft(null)}>{tr("取消")}</button>
          <button className="cv-btn cv-btn--sm cv-btn--primary" type="submit" disabled={busy || !!stale || !note.trim()}>{tr("保存")}</button>
        </div>
      </form>
    </dialog>}
  </>;
}
