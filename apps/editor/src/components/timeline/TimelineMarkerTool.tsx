import { BookmarkPlus } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { tr, useLocale } from "../../i18n";
import { getLatestState, runCommand } from "../../store/actions";
import { type EditorState, useEditor } from "../../store/editor";
import { rationalToSecs } from "../../lib/rational";
import { fmtTimePrecise, secsToFrameRatString } from "./util";

// Keep one pending command per project even if its dialog or workspace closes.
const pendingMarkerSaves = new Set<string>();
const markerSaveListeners = new Set<() => void>();
function subscribeMarkerSaves(listener: () => void) {
  markerSaveListeners.add(listener);
  return () => { markerSaveListeners.delete(listener); };
}
function setMarkerPending(projectId: string, pending: boolean) {
  if (pending) pendingMarkerSaves.add(projectId);
  else pendingMarkerSaves.delete(projectId);
  markerSaveListeners.forEach(listener => listener());
}

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
  const busy = useSyncExternalStore(subscribeMarkerSaves, () => !!state.currentId && pendingMarkerSaves.has(state.currentId));
  const [rejected, setRejected] = useState(false);
  const mounted = useRef(true);
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const label = tr("添加审片标记");

  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!draft) return;
    const element = dialog.current;
    if (!element) return;
    element.showModal();
    field.current?.focus();
    return () => {
      element.close();
      const button = trigger.current;
      if (button?.disabled) button.closest(".timeline")?.querySelector<HTMLElement>(".timeline-ruler")?.focus();
      else button?.focus();
    };
  }, [draft]);

  useLayoutEffect(() => {
    // The existing editor lease intercepts all keyboard events. Do not leave
    // its newly read-only workspace covered by a native modal.
    if (draft && (state.editLock || (!busy && !matchesDraft(state, draft)))) setDraft(null);
  }, [busy, draft, state]);

  const open = () => {
    const latest = getLatestState() ?? state;
    const project = latest.project;
    if (!project || pendingMarkerSaves.has(project.projectId) || !canWriteMarker(latest) || latest.currentId !== state.currentId ||
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
    if (!draft || !name || name.length > 120 || /[\r\n]/.test(name) || pendingMarkerSaves.has(draft.projectId) || rejected) return;
    const latest = getLatestState() ?? state;
    if (!matchesDraft(latest, draft)) {
      setRejected(true);
      return;
    }
    const isCurrent = () => {
      const current = getLatestState() ?? state;
      return mounted.current && current.currentId === draft.projectId &&
        current.project?.sequence.id === draft.sequenceId && current.view === "editor";
    };
    setMarkerPending(draft.projectId, true);
    try {
      const result = await runCommand(dispatch, latest, "marker.add", { name, time: draft.time },
        { autoShow: false, retryConflict: false, isCurrent });
      if (!isCurrent()) return;
      if (result.ok) setDraft(null);
      else setRejected(true);
    } finally {
      setMarkerPending(draft.projectId, false);
    }
  };

  const stale = draft && (rejected || (!busy && !matchesDraft(state, draft)));
  return <>
    <button ref={trigger} type="button" className="timeline__zoom-btn timeline-marker-tool" disabled={busy || !canWriteMarker(state) || !(end > 0)}
      aria-label={label} title={label} aria-haspopup="dialog" onClick={open}><BookmarkPlus size={14} aria-hidden="true" /></button>
    {draft && <dialog ref={dialog} className="timeline-marker-dialog" aria-labelledby={titleId}
      onCancel={event => { event.preventDefault(); setDraft(null); }}>
      <form onSubmit={event => { event.preventDefault(); void submit(); }}>
        <strong id={titleId}>{label}</strong>
        <time>{fmtTimePrecise(rationalToSecs(draft.time))}</time>
        <label>{tr("标记备注")}
          <input ref={field} className="cv-input" type="text" value={note} maxLength={120} disabled={busy}
            onChange={event => setNote(event.currentTarget.value)} />
        </label>
        {busy && <p role="status">{tr("保存中…")}</p>}
        {stale && !busy ? <p role="status">{tr("工程已变化或暂不可编辑，请关闭后重新添加标记。")}</p> : null}
        <div className="timeline-marker-dialog__actions">
          <button className="cv-btn cv-btn--sm" type="button" onClick={() => setDraft(null)}>{busy ? tr("关闭") : tr("取消")}</button>
          <button className="cv-btn cv-btn--sm cv-btn--primary" type="submit" disabled={busy || !!stale || !note.trim()}>{tr("保存")}</button>
        </div>
      </form>
    </dialog>}
  </>;
}
