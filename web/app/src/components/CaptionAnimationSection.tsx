/** 字幕入场/出场动画预设（I03）：数据驱动预设选择器，写入后端 caption.update 的
 *  animIn / animOut（毫秒）及 animLoopStyle / animLoopMs（循环样式与周期）。
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
  animInStyle: NonNullable<Caption["animInStyle"]>;
}

const ANIM_PRESETS: AnimPreset[] = [
  { id: "none", label: "无", animIn: 0, animOut: 0, animInStyle: "fade" },
  { id: "in-fast", label: "淡入 300ms", animIn: 300, animOut: 0, animInStyle: "fade" },
  { id: "in-mid", label: "淡入 500ms", animIn: 500, animOut: 0, animInStyle: "fade" },
  { id: "in-slow", label: "淡入 800ms", animIn: 800, animOut: 0, animInStyle: "fade" },
  { id: "typewriter-fast", label: "逐字入场 800ms", animIn: 800, animOut: 0, animInStyle: "typewriter" },
  { id: "typewriter-mid", label: "逐字入场 1.5s", animIn: 1500, animOut: 0, animInStyle: "typewriter" },
  { id: "typewriter-slow", label: "逐字入场 3s", animIn: 3000, animOut: 0, animInStyle: "typewriter" },
  { id: "typewriter-long", label: "逐字入场 5s", animIn: 5000, animOut: 0, animInStyle: "typewriter" },
  { id: "typewriter-very-long", label: "逐字入场 8s", animIn: 8000, animOut: 0, animInStyle: "typewriter" },
  { id: "out-fast", label: "淡出 300ms", animIn: 0, animOut: 300, animInStyle: "fade" },
  { id: "out-mid", label: "淡出 500ms", animIn: 0, animOut: 500, animInStyle: "fade" },
  { id: "out-slow", label: "淡出 800ms", animIn: 0, animOut: 800, animInStyle: "fade" },
  { id: "inout-mid", label: "淡入+淡出 500ms", animIn: 500, animOut: 500, animInStyle: "fade" },
];

/** 由当前 animIn/animOut 反查命中的预设 id（无精确匹配则回退「无」）。 */
function presetIdOf(animIn: number, animOut: number, animInStyle: string): string {
  const hit = ANIM_PRESETS.find((p) => p.animIn === animIn && p.animOut === animOut
    && p.animInStyle === animInStyle);
  if (hit) return hit.id;
  return animInStyle === "typewriter" ? "typewriter-custom" : "none";
}

export function CaptionAnimationSection({ caption, disabled = false }: { caption: Caption; disabled?: boolean }) {
  const { state, dispatch } = useEditor();
  const currentIn = caption.animIn ?? 0;
  const currentOut = caption.animOut ?? 0;
  const [busy, setBusy] = useState(false);

  const currentInStyle = caption.animInStyle ?? "fade";
  const currentLoopStyle = caption.animLoopStyle ?? "none";
  const currentLoopMs = caption.animLoopMs ?? 1000;
  const currentId = useMemo(
    () => presetIdOf(currentIn, currentOut, currentInStyle),
    [currentIn, currentOut, currentInStyle],
  );

  const apply = async (preset: AnimPreset) => {
    if (busy) return;
    setBusy(true);
    const res = await updateCaption(dispatch, state, {
      captionId: caption.id,
      animIn: preset.animIn,
      animOut: preset.animOut,
      animInStyle: preset.animInStyle,
    });
    if (!res.ok) {
      showError(dispatch, res.error ?? new Error("字幕动画更新失败"));
    }
    setBusy(false);
  };

  const applyLoop = async (animLoopStyle: NonNullable<Caption["animLoopStyle"]>,
                           animLoopMs = currentLoopMs) => {
    if (busy) return;
    setBusy(true);
    const res = await updateCaption(dispatch, state, {
      captionId: caption.id, animLoopStyle, animLoopMs,
    });
    if (!res.ok) showError(dispatch, res.error ?? new Error("字幕循环动画更新失败"));
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
        disabled={busy || disabled}
        onChange={(e) => {
          const preset = ANIM_PRESETS.find((p) => p.id === e.target.value);
          if (preset) void apply(preset);
        }}
      >
        {currentId === "typewriter-custom" && (
          <option value="typewriter-custom">逐字入场（自定义时长）</option>
        )}
        {ANIM_PRESETS.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
          </option>
        ))}
      </select>
      <div className="inspector__subrow" style={{ marginTop: 8 }}>
        <span className="inspector__key">循环动画</span>
      </div>
      <select
        value={currentLoopStyle}
        aria-label="字幕循环动画"
        className="cv-input"
        style={{ height: 28, width: "100%" }}
        disabled={busy || disabled}
        onChange={(e) => {
          const style = e.target.value as NonNullable<Caption["animLoopStyle"]>;
          void applyLoop(style);
        }}
      >
        <option value="none">无</option>
        <option value="pulse">呼吸缩放</option>
        <option value="blink">柔和闪烁</option>
      </select>
      {currentLoopStyle !== "none" && (
        <select
          value={currentLoopMs}
          aria-label="字幕循环周期"
          className="cv-input"
          style={{ height: 28, width: "100%", marginTop: 6 }}
          disabled={busy || disabled}
          onChange={(e) => void applyLoop(currentLoopStyle, Number(e.target.value))}
        >
          {[300, 500, 800, 1000, 1500, 2000, 3000].map((ms) => (
            <option key={ms} value={ms}>循环周期 {ms >= 1000 ? `${ms / 1000}s` : `${ms}ms`}</option>
          ))}
        </select>
      )}
      <p className="cv-hint" style={{ marginTop: 4 }}>
        逐字入场可选 0.8–8 秒；循环动画在入场后持续到出场前，预览与导出同步。
      </p>
    </>
  );
}
