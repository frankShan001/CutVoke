/** J12 套用确认对话框：在 TemplateGallery 内弹层。
    明确展示 clearExisting 语义（追加 vs 清空重建，默认 false），套用中禁用按钮防重复提交。 */

import { AlertTriangle, ArrowRight, CheckCircle2, RefreshCw } from "lucide-react";
import { Button } from "./ui";
import type { Template } from "../types/api";

export function TemplateApplyDialog({
  template,
  inEditor,
  clearExisting,
  onClearExistingChange,
  applying,
  applyErr,
  busyOther,
  onConfirm,
  onCancel,
}: {
  template: Template;
  inEditor: boolean;
  clearExisting: boolean;
  onClearExistingChange: (v: boolean) => void;
  applying: boolean;
  applyErr: string | null;
  busyOther: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="tpl-confirm" role="dialog" aria-modal="true" aria-label="确认套用模板">
      <div className="tpl-confirm__backdrop" onClick={onCancel} />
      <div className="tpl-confirm__box">
        <h3 className="tpl-confirm__title">套用模板《{template.name}》</h3>
        {inEditor ? (
          <label className="tpl-confirm__row">
            <input
              type="checkbox"
              checked={clearExisting}
              onChange={(e) => onClearExistingChange(e.target.checked)}
            />
            <span>先清空当前时间线再套用（默认关闭 = 在现有内容后追加，不破坏已有工程）</span>
          </label>
        ) : (
          <p className="tpl-confirm__note">
            将从模板画幅新建工程《{template.name}》并套用，进入编辑器即可直接导出。
          </p>
        )}
        {applyErr && (
          <div className="tpl-confirm__err" role="alert">
            <AlertTriangle size={13} /> {applyErr}
          </div>
        )}
        <div className="tpl-confirm__actions">
          <Button variant="ghost" size="sm" onClick={onCancel}>
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={onConfirm}
            disabled={applying}
            aria-label="确认套用模板"
          >
            {applying ? (
              <>
                <RefreshCw size={13} className="tpl-spin" /> 套用中…
              </>
            ) : (
              <>
                <ArrowRight size={13} /> 确认套用
              </>
            )}
          </Button>
        </div>
        {!applying && busyOther && (
          <div className="tpl-confirm__busy">
            <CheckCircle2 size={13} /> 正在套用其他模板，请稍候
          </div>
        )}
      </div>
    </div>
  );
}
