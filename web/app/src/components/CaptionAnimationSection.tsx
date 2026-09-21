/** 字幕入场/出场动画预设（I03）：数据驱动预设选择器，写入后端 caption.update 的
 *  animIn / animOut（毫秒，淡入/淡出）。后端 caption_render 用 \fad(in,out) 实现。
 *
 *  说明：字幕（caption）不走片段效果系统（cutvoke.anim.* 仅适用于 image/video 片段，
 *  且文字轨转 Caption 时不携带动画效果），故字幕的入出场动画用 caption 自带的
 *  animIn/animOut 字段表达。预设为「淡入/淡出时长组合」，是数据驱动的本地常量
 *  （非硬编码效果 ID 数组），并随后端动画能力可扩展。 */

import { useMemo, useState } from "react";
import { Sparkles } from "lucide-react";
import { useEditor, showError } from "../store/editor";
import { updateCaption } from "../store/clipEdit";
import type { Caption } from "../types/api";

/** 入出场动画预设：入场 animIn / 出场 animOut（毫秒）。0=无。 */
interface AnimPreset {
  id: string;
  label: string;
  animIn: number;
  animOut: number;
}

const ANIM_PRESETS: AnimPreset[] = [
  { id: "none", label: "无", animIn: 0, animOut: 0 },
  { id: "in-fast", label: "淡入 300ms", animIn: 300, animOut: 0 },
  { id: "in-mid", label: "淡入 500ms", animIn: 500, animOut: 0 },
  { id: "in-slow", label: "淡入 800ms", animIn: 800, animOut: 0 },
  { id: "out-fast", label: "淡出 300ms", animIn: 0, animOut: 300 },
  { id: "out-mid", label: "淡出 500ms", animIn: 0, animOut: 500 },
  { id: "out-slow", label: "淡出 800ms", animIn: 0, animOut: 800 },
  { id: "inout-mid", label: "淡入+淡出 500ms", animIn: 500, animOut: 500 },
];

/** 由当前 animIn/animOut 反查命中的预设 id（无精确匹配则回退「无」）。 */
function presetIdOf(animIn: number, animOut: number): string {
  const hit = ANIM_PRESETS.find((p) => p.animIn === animIn && p.animOut === animOut);
  return hit ? hit.id : "none";
}

export function CaptionAnimationSection({ caption }: { caption: Caption }) {
  const { state, dispatch } = useEditor();
  const currentIn = caption.animIn ?? 0;
  const currentOut = caption.animOut ?? 0;
  const [busy, setBusy] = useState(false);

  const currentId = useMemo(() => presetIdOf(currentIn, currentOut), [currentIn, currentOut]);

  const apply = async (preset: AnimPreset) => {
    if (busy) return;
    setBusy(true);
    const res = await updateCaption(dispatch, state, {
      captionId: caption.id,
      animIn: preset.animIn,
      animOut: preset.animOut,
    });
    if (!res.ok) {
      showError(dispatch, res.error ?? new Error("字幕动画更新失败"));
    }
    setBusy(false);
  };

  return (
    <>
      <div className="inspector__sep" />
      <div className="inspector__subrow">
        <span className="inspector__key">
          <Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          入场/出场动画
        </span>
      </div>
      <select
        value={currentId}
        aria-label="字幕入场出场动画"
        className="cv-input"
        style={{ height: 28, width: "100%" }}
        disabled={busy}
        onChange={(e) => {
          const preset = ANIM_PRESETS.find((p) => p.id === e.target.value);
          if (preset) void apply(preset);
        }}
      >
        {ANIM_PRESETS.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
          </option>
        ))}
      </select>
      <p className="cv-hint" style={{ marginTop: 4 }}>
        入场（淡入）/出场（淡出）时长，导出与预览均生效。
      </p>
    </>
  );
}
