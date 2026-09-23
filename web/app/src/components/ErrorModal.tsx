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

function explainError(code: string, message: string): { message: string; action: string } {
  const overlap = message.match(/clip would overlap another clip on track\s+(.+)/i);
  if (code === "INVALID_ARGUMENT" && overlap) {
    return {
      message: `片段与轨道 ${overlap[1]} 上的其他片段时间重叠。`,
      action: "请把片段移到空白位置，或放到另一条同类型轨道。工程没有被修改。",
    };
  }
  if (code === "INVALID_ARGUMENT") {
    return {
      message: "这项操作不符合当前工程的编辑规则。",
      action: "请检查片段时间、轨道类型和素材范围后重试。工程没有被修改。",
    };
  }
  if (code === "REVISION_CONFLICT") {
    return {
      message: "工程在你开始操作后已被其他编辑更新。",
      action: "先确认最新画面，再重新执行这次修改。",
    };
  }
  return {
    message,
    action: "可展开技术信息查看原始错误和诊断数据。",
  };
}

export function ErrorModal() {
  const { state, dispatch } = useEditor();
  const err = state.error;
  if (!err) return null;

  const close = () => dispatch({ type: "ERROR_SET", error: null });
  const explanation = explainError(err.code, err.message);

  return (
    <div className="error-modal" role="alertdialog" aria-label="操作未完成">
      <div className="error-modal__head">
        <TriangleAlert size={16} />
        操作未完成
        <button className="error-modal__close" onClick={close} aria-label="关闭">
          <X size={16} />
        </button>
      </div>
      <p className="error-modal__message">{explanation.message}</p>
      <p className="error-modal__action">{explanation.action}</p>
      <div className="error-modal__meta">
        <span className="cv-hint">
          {err.committed
            ? "这次修改已经写入工程。"
            : "这次操作没有改动工程，可以按上面的提示调整后再试。"}
        </span>
      </div>
      <details className="error-modal__technical">
        <summary>技术详情</summary>
        <div className="error-modal__technical-body">
          <div>
            <span className="error-modal__code">{err.code}</span>
            <span className="error-modal__code" style={{ marginLeft: 6, background: "var(--accent-soft)", color: "var(--accent-text)" }}>
              HTTP {err.status}
            </span>
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
          <div className="k">原始错误：{err.message}</div>
          <div className="k">details</div>
          <pre className="error-modal__detail">{detailText(err.details)}</pre>
        </div>
      </details>
    </div>
  );
}
