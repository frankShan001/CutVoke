import { tr, useLocale } from "../../i18n";
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
  useLocale();
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
          {isTransition ? tr("转场 · ") : ""}
          {effectLabel(catalog, id)}
        </span>
        <span className="effect-row__meta">
          {effect.version ? `v${effect.version}` : ""}
          {!enabled ? tr(" · 已旁路") : ""}
        </span>
      </div>
      {summary ? <div className="effect-row__params">{summary}</div> : null}
      <div className="effect-row__actions">
        <button
          type="button"
          className="effect-row__btn"
          disabled={index === 0 || busy}
          aria-label={tr("上移 {0}", effectLabel(catalog, id))}
          title={tr("上移")}
          onClick={() => onMove(-1)}
        >
          <ChevronUp size={13} />
        </button>
        <button
          type="button"
          className="effect-row__btn"
          disabled={index === total - 1 || busy}
          aria-label={tr("下移 {0}", effectLabel(catalog, id))}
          title={tr("下移")}
          onClick={() => onMove(1)}
        >
          <ChevronDown size={13} />
        </button>
        <button
          type="button"
          className={`effect-row__btn effect-row__btn--bypass ${!enabled ? "effect-row__btn--on" : ""}`}
          disabled={busy}
          aria-pressed={!enabled}
          aria-label={tr("{0} {1}", enabled ? tr("旁路") : tr("恢复"), effectLabel(catalog, id))}
          title={enabled ? tr("旁路（保留在栈中但不参与渲染）") : tr("恢复该效果")}
          onClick={onBypass}
        >
          <Layers size={13} />
        </button>
        <button
          type="button"
          className="effect-row__btn effect-row__btn--danger"
          disabled={busy}
          aria-label={tr("删除 {0}", effectLabel(catalog, id))}
          title={tr("删除效果")}
          onClick={onRemove}
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}
