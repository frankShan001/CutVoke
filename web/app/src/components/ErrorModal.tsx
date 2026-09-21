/** 结构化错误浮窗：HTTP 码 / 错误码 / message / 已提交 / 可重试 / 关联 ID / details。
    指向 AI 协同语义：committed 表示操作是否已落库（Agent 协作时重要）。 */

import { X, TriangleAlert } from "lucide-react";
import { useEditor } from "../store/editor";

function fmtBoolean(v: boolean | undefined | null): string {
  if (v === undefined || v === null) return "—";
  return v ? "是" : "否";
}

function detailText(details: unknown): string {
  if (details === undefined || details === null) return "（无）";
  if (typeof details === "string") return details;
  try {
    return JSON.stringify(details, null, 2);
  } catch {
    return String(details);
  }
}

export function ErrorModal() {
  const { state, dispatch } = useEditor();
  const err = state.error;
  if (!err) return null;

  const close = () => dispatch({ type: "ERROR_SET", error: null });

  return (
    <div className="error-modal" role="alertdialog" aria-label="操作错误">
      <div className="error-modal__head">
        <TriangleAlert size={16} />
        操作失败
        <button className="error-modal__close" onClick={close} aria-label="关闭">
          <X size={16} />
        </button>
      </div>
      <div>
        <span className="error-modal__code">{err.code}</span>
        <span className="error-modal__code" style={{ marginLeft: 6, background: "var(--accent-soft)", color: "var(--accent-text)" }}>
          HTTP {err.status}
        </span>
      </div>
      <div className="error-modal__meta" style={{ marginTop: 4 }}>
        <span>{err.message}</span>
      </div>
      <div className="error-modal__meta">
        <span>
          <span className="k">已提交：</span>
          <span className={err.committed ? "error-modal__ok-flag" : "error-modal__err-flag"}>
            {fmtBoolean(err.committed)}
          </span>
        </span>
        <span>
          <span className="k">可重试：</span>
          {fmtBoolean(err.retryable)}
        </span>
        <span>
          <span className="k">关联 ID：</span>
          <span className="cv-mono">{err.correlationId || "—"}</span>
        </span>
      </div>
      <div className="error-modal__meta">
        <span className="cv-hint">
          {err.committed
            ? "该操作已提交至工程（Agent 可读到变更）。发起新操作前，编辑器已自动刷新到最新 revision。"
            : err.retryable
              ? "该操作未提交，可安全重试（建议先刷新）。"
              : "该操作被拒绝，未产生变更。"}
        </span>
      </div>
      <div>
        <span className="k" style={{ color: "var(--text-dim)", fontSize: 12 }}>details：</span>
        <pre className="error-modal__detail">{detailText(err.details)}</pre>
      </div>
    </div>
  );
}