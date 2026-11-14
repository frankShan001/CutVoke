import { useEffect, useRef, useState } from "react";
import { tr, useLocale } from "../i18n";
import { reviewEditLease, type EditLease, type EditLeaseReview } from "../lib/api";
import { useEditor } from "../store/editor";
import { takeOverEditing } from "../store/actions";
import { Button } from "./ui";

/** One observed lease per mounted notice; replacement leases require a new confirmation. */
export function EditLeaseNotice({ projectId, lease }: { projectId: string; lease: EditLease }) {
  useLocale();
  const { dispatch } = useEditor();
  const [now, setNow] = useState(Date.now());
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [review, setReview] = useState<EditLeaseReview | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewFailed, setReviewFailed] = useState(false);
  const reviewPending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const loadReview = async () => {
    if (reviewPending.current) return;
    reviewPending.current = true; setReviewBusy(true); setReviewFailed(false); setReview(null);
    try {
      const result = await reviewEditLease(projectId, lease.leaseId);
      if (mounted.current && result.projectId === projectId && result.leaseId === lease.leaseId) setReview(result);
    } catch { if (mounted.current) setReviewFailed(true); }
    finally { reviewPending.current = false; if (mounted.current) setReviewBusy(false); }
  };
  const pending = useRef(false);
  const entry = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => { if (confirm) cancel.current?.focus(); }, [confirm]);
  const dismiss = () => { if (!pending.current) { setConfirm(false); entry.current?.focus(); } };
  const takeOver = async () => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setFailed(false);
    try { setFailed(!await takeOverEditing(dispatch, projectId, lease.leaseId)); }
    finally { pending.current = false; setBusy(false); }
  };
  return <div className="agent-edit-lock" role="status" aria-live="polite">
    <div className="agent-edit-lock__card">
      <strong>{tr("Agent 正在编辑")}</strong>
      <span>{lease.owner}{tr(" · 页面暂时只读")}</span>
      <small>{tr("租约剩余 {0} 秒", Math.max(0, Math.ceil(lease.expiresAt - now / 1000)))}</small>
      <button ref={entry} type="button" className="cv-btn cv-btn--secondary cv-btn--sm" onClick={() => setConfirm(true)} disabled={busy || confirm}>{tr("接管编辑")}</button>
      <button type="button" className="cv-btn cv-btn--secondary cv-btn--sm" disabled={busy || reviewBusy}
        onClick={() => void loadReview()}>{reviewBusy ? tr("正在读取…") : tr("查看本次租约的修改")}</button>
      {reviewFailed ? <p role="alert">{tr("无法读取修改，请刷新工程后重试。")}</p> : null}
      {review ? <section aria-label={tr("查看本次租约的修改")} className="agent-edit-lock__review">
        {!review.available ? <p>{tr("未记录此租约的起始版本。")}</p> : <>
          <p>{tr("租约版本 {0} → {1}", review.acquiredRevision, review.revision)}</p>
          <small>{tr("仅包含版本化编辑，不含重命名、导出或其他无版本操作。")}</small>
          {!review.changes.length ? <p>{tr("没有已提交的修改。")}</p> : <ol>
            {review.changes.map((change, index) => <li key={`${change.revision}:${index}`}>
              {tr("版本 {0} → {1} · 命令 {2} · {3} 个对象", change.previousRevision, change.revision, change.commandId || "—", change.entityCount)}
              <div>{change.entityKinds.map(kind => `${kind.type}: ${kind.change} ×${kind.count}`).join(" · ")}</div>
            </li>)}
          </ol>}
          {review.hasMore ? <p>{tr("仅显示最近的修改。")}</p> : null}
        </>}
      </section> : null}
      {confirm ? <div className="agent-edit-lock__confirm" role="alertdialog" aria-label={tr("确认接管编辑")}
        onKeyDown={e => { if (e.key === "Escape") dismiss(); }}>
        <p>{tr("将结束此 Agent 的租约并刷新工程。已完成的修改会保留，旧请求将被拒绝。")}</p>
        <button ref={cancel} type="button" className="cv-btn cv-btn--secondary cv-btn--sm" onClick={dismiss} disabled={busy}>{tr("取消")}</button>
        <Button size="sm" onClick={() => void takeOver()} disabled={busy}>{busy ? tr("正在接管…") : tr("确认接管")}</Button>
        {failed ? <p role="alert">{tr("接管未完成，请检查当前租约并重试。")}</p> : null}
      </div> : null}
    </div>
  </div>;
}
