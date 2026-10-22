import { useEditor } from "../store/editor";
import { interactionStatus, useInteractionVersion } from "../store/interactive";
import { flushPendingEdits } from "../lib/pendingEdits";
import { tr } from "../i18n";

export function InteractionStatus() {
  const { state } = useEditor({ subscribeToClock: false });
  useInteractionVersion();
  const status = interactionStatus(state.currentId || "");
  if (!status.pending) return null;
  return <span className="player__edit-status" role="status">
    {status.failed ? tr("保存失败，请重试") : tr("自动保存中…")}
    {status.failed ? <button type="button" className="cv-chip" onClick={() => void flushPendingEdits()}>{tr("重试")}</button> : null}
  </span>;
}
