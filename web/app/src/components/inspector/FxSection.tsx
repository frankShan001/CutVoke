/** 画面特效小节（J01 数据驱动）：滤镜清单 + 参数全部来自注册表 category=fx。
 *  滤镜可叠加：每个 toggle 独立增删（按 effectId 精确）；调参走「先 remove 再 add」
 *  保证该 effectId 只有一个实例（沿用 Inspector 旧逻辑）。 */
import { useEffect, useMemo, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { showError, useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { addEffect, removeEffect } from "../../store/clipEdit";
import {
  effectsByCategory,
  getEffectCatalog,
  iconForCategory,
  type EffectSpec,
} from "../../lib/effects";
import { paramControls } from "../../lib/effectControls";
import { ParamControls } from "./ParamControls";
import type { Clip } from "../../types/api";

export function FxSection({ clip }: { clip: Clip }) {
  const { state, dispatch } = useEditor();
  const [specs, setSpecs] = useState<EffectSpec[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getEffectCatalog()
      .then((all) => {
        if (!cancelled) setSpecs(effectsByCategory(all, "fx"));
      })
      .catch((err) => {
        if (!cancelled) showError(dispatch, err);
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  const isActive = (id: string) => clip.effects?.some((e) => e.effectId === id) ?? false;
  const paramsOf = (id: string) =>
    (clip.effects?.find((e) => e.effectId === id)?.params as Record<string, unknown>) ?? {};

  const toggle = async (spec: EffectSpec) => {
    if (busy) return;
    setBusy(true);
    const ctx = getLatestState() || state;
    if (isActive(spec.effectId)) {
      await removeEffect(dispatch, ctx, { clipId: clip.id, effectId: spec.effectId });
    } else {
      await addEffect(dispatch, ctx, {
        clipId: clip.id,
        effectId: spec.effectId,
        params: { ...spec.defaults },
      });
    }
    setBusy(false);
  };

  const setParams = async (spec: EffectSpec, params: Record<string, unknown>) => {
    if (busy) return;
    setBusy(true);
    const ctx = getLatestState() || state;
    const r = await removeEffect(dispatch, ctx, { clipId: clip.id, effectId: spec.effectId });
    if (r.ok) {
      const ctx2 = getLatestState() || ctx;
      await addEffect(dispatch, ctx2, {
        clipId: clip.id,
        effectId: spec.effectId,
        params,
      });
    }
    setBusy(false);
  };

  const Icon = iconForCategory("fx");
  const activeSpecs = specs.filter((s) => isActive(s.effectId));

  return (
    <>
      <div className="inspector__sep" />
      <div className="inspector__subrow">
        <span className="inspector__key">
          <SlidersHorizontal size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          画面特效
        </span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
        {specs.map((s) => {
          const on = isActive(s.effectId);
          return (
            <button
              key={s.effectId}
              type="button"
              aria-label={s.name}
              aria-pressed={on}
              className={"cv-chip" + (on ? " cv-chip--on" : "")}
              title={s.description}
              onClick={() => void toggle(s)}
            >
              <Icon size={12} /> {s.name}
            </button>
          );
        })}
      </div>
      {activeSpecs.map((s) => (
        <FxParams
          key={s.effectId}
          spec={s}
          params={paramsOf(s.effectId)}
          onCommit={(p) => void setParams(s, p)}
        />
      ))}
    </>
  );
}

/** 单个已生效滤镜的参数编辑器（本地态，提交时才发命令）。 */
function FxParams({
  spec,
  params,
  onCommit,
}: {
  spec: EffectSpec;
  params: Record<string, unknown>;
  onCommit: (next: Record<string, unknown>) => void;
}) {
  const [local, setLocal] = useState<Record<string, unknown>>(params);
  const paramKey = JSON.stringify(params);
  useEffect(() => {
    setLocal(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec.effectId, paramKey]);

  const controls = useMemo(() => paramControls(spec, local), [spec, local]);

  return (
    <ParamControls
      controls={controls}
      onChange={(name, value) => {
        const next = { ...local };
        if (value === undefined) delete next[name];
        else next[name] = value;
        setLocal(next);
      }}
      onCommit={() => onCommit(local)}
    />
  );
}
