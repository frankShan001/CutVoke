/** 检查器复用小组件（J01 拆分自 Inspector.tsx，保持行为与 aria 契约不变）。 */
import { useState } from "react";
import { Plus } from "lucide-react";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { addEffect } from "../../store/clipEdit";
import { BUILTIN_TRANSFORM_ID, BUILTIN_COLOR_ID, BUILTIN_CROSSFADE_ID } from "../../lib/effects";

/** 快捷「加效果」小按钮（片段属性区底部）。 */
export function EffChip({
  label,
  effectId,
  clipId,
}: {
  label: string;
  effectId: string;
  clipId: string;
}) {
  const { state, dispatch } = useEditor();
  const [busy, setBusy] = useState(false);
  const onAdd = () => {
    if (busy) return;
    setBusy(true);
    const ctx = getLatestState() || state;
    void addEffect(dispatch, ctx, { clipId, effectId, params: defaultParams(effectId) }).finally(() =>
      setBusy(false),
    );
  };
  return (
    <button className="cv-chip" onClick={onAdd} title={`添加${label}`}>
      <Plus size={10} /> {label}
    </button>
  );
}

export function defaultParams(effectId: string): Record<string, unknown> {
  switch (effectId) {
    case BUILTIN_TRANSFORM_ID:
      return { position: { x: 0, y: 0 }, scale: 1.0, rotation: 0, opacity: 1.0 };
    case BUILTIN_COLOR_ID:
      return { brightness: 0.0, contrast: 1.0, saturation: 1.0 };
    case BUILTIN_CROSSFADE_ID:
      return { duration: 1.0 };
    default:
      return {};
  }
}
