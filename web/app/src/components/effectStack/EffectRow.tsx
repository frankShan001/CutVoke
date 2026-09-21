/** 效果条单行（J01）：中文名 / 参数摘要 / 上移下移 / 旁路开关 / 删除。 */
import { ChevronUp, ChevronDown, Layers, Trash2 } from "lucide-react";
import { effectLabel, type EffectSpec } from "../../lib/effects";
import { paramsSummary } from "../../lib/effectControls";
import type { EffectInstance } from "../../types/api";

export function EffectRow({
  effect,
  index,
  total,
  catalog,
  busy,
  onMove,
  onBypass,
  onRemove,
}: {
  effect: EffectInstance;
  index: number;
  total: number;
  catalog: EffectSpec[];
  busy: boolean;
  onMove: (dir: -1 | 1) => void;
  onBypass: () => void;
  onRemove: () => void;
}) {
  const id = String(effect.effectId);
  const enabled = effect.enabled !== false;
  const isTransition = id.startsWith("cutvoke.transition.");
  const spec = catalog.find((s) => s.effectId === id);
  const summary = spec ? paramsSummary(spec, effect.params) : "";

  return (
    <div className={`effect-row${!enabled ? " effect-row--bypassed" : ""}`}>
      <div className="effect-row__head">
        <span className="effect-row__idx">{index + 1}</span>
        <span className="effect-row__name" title={id}>
          {isTransition ? "转场 · " : ""}
          {effectLabel(catalog, id)}
        </span>
        <span className="effect-row__meta">
          {effect.version ? `v${effect.version}` : ""}
          {!enabled ? " · 已旁路" : ""}
        </span>
      </div>
      {summary ? <div className="effect-row__params">{summary}</div> : null}
      <div className="effect-row__actions">
        <button
          type="button"
          className="effect-row__btn"
          disabled={index === 0 || busy}
          aria-label={`上移 ${effectLabel(catalog, id)}`}
          title="上移"
          onClick={() => onMove(-1)}
        >
          <ChevronUp size={13} />
        </button>
        <button
          type="button"
          className="effect-row__btn"
          disabled={index === total - 1 || busy}
          aria-label={`下移 ${effectLabel(catalog, id)}`}
          title="下移"
          onClick={() => onMove(1)}
        >
          <ChevronDown size={13} />
        </button>
        <button
          type="button"
          className={`effect-row__btn effect-row__btn--bypass ${!enabled ? "effect-row__btn--on" : ""}`}
          aria-pressed={!enabled}
          aria-label={`${enabled ? "旁路" : "恢复"} ${effectLabel(catalog, id)}`}
          title={enabled ? "旁路（保留在栈中但不参与渲染）" : "恢复该效果"}
          onClick={onBypass}
        >
          <Layers size={13} />
        </button>
        <button
          type="button"
          className="effect-row__btn effect-row__btn--danger"
          disabled={busy}
          aria-label={`删除 ${effectLabel(catalog, id)}`}
          title="删除效果"
          onClick={onRemove}
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}