/** 花字样式库（J04）：在字幕面板内一键套用「标语样式」预设，替代逐项手调。
 *  数据来自 lib/fontStyles.ts 的预设数组；点击预设 → caption.update 套用整组 style；
 *  「清除样式」→ 显式传 CLEAR_CAPTION_STYLE（后端缺省字段不变，故清除即置默认）。 */

import { Sparkles, Eraser } from "lucide-react";
import { Button, Row } from "./ui";
import { useEditor } from "../store/editor";
import { updateCaption } from "../store/clipEdit";
import {
  FONT_STYLE_PRESETS,
  CLEAR_CAPTION_STYLE,
  type FontStylePreset,
} from "../lib/fontStyles";

export function FontStylePanel({ captionId }: { captionId: string | null }) {
  const { state, dispatch } = useEditor();

  const applyPreset = (preset: FontStylePreset) => {
    if (!captionId) return;
    void updateCaption(dispatch, state, { captionId, ...preset.style });
  };

  const clearStyle = () => {
    if (!captionId) return;
    void updateCaption(dispatch, state, { captionId, ...CLEAR_CAPTION_STYLE });
  };

  if (!captionId) {
    return (
      <div className="cv-style-edit" aria-live="polite">
        <div className="cv-style-edit__hint">选择一条字幕后，可一键套用花字样式</div>
      </div>
    );
  }

  return (
    <div className="cv-style-edit" aria-label="花字样式库">
      <div className="cv-style-edit__hint">
        <Sparkles size={13} /> 一键套用标语样式
      </div>
      <div className="font-style-grid" role="group" aria-label="花字预设">
        {FONT_STYLE_PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            className="font-style-card"
            onClick={() => applyPreset(p)}
            title={p.description}
            aria-label={`套用花字样式 ${p.name}`}
          >
            <span
              className="font-style-card__preview"
              style={{
                color: p.style.color,
                WebkitTextStroke: `${p.style.strokeWidth}px ${p.style.strokeColor}`,
                background: p.style.background || "transparent",
                fontWeight: p.style.bold ? 700 : 400,
                fontStyle: "normal",
              }}
            >
              字幕
            </span>
            <span className="font-style-card__name">{p.name}</span>
          </button>
        ))}
      </div>
      <Row className="font-style-clear-row">
        <Button
          variant="ghost"
          size="sm"
          full
          onClick={clearStyle}
          aria-label="清除字幕样式"
        >
          <Eraser size={13} />
          清除样式
        </Button>
      </Row>
    </div>
  );
}
