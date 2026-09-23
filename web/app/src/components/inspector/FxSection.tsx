/** 片段特效小节：按素材适用类型筛选；调参一次原子更新。 */
import { useEffect, useMemo, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { showError, useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { addEffect, removeEffect } from "../../store/clipEdit";
import { updateEffect } from "../../store/effectEdit";
import {
  effectsByCategory,
  getEffectCatalog,
  iconForCategory,
  type EffectSpec,
} from "../../lib/effects";
import { paramControls } from "../../lib/effectControls";
import { ParamControls } from "./ParamControls";
import { inferKind } from "../../lib/assetStore";
import type { Clip } from "../../types/api";

export function FxSection({ clip, trackKind }: { clip: Clip; trackKind: string }) {
  const { state, dispatch } = useEditor();
  const [specs, setSpecs] = useState<EffectSpec[]>([]);
  const [busy, setBusy] = useState(false);
  const mediaKind = inferKind(clip.assetRef.sourcePath, false, false);
  const targetTypes = trackKind === "audio" ? ["audio"]
    : trackKind === "text" ? ["text"]
    : mediaKind === "image" ? ["image"]
    : ["video", "audio"];

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
  const applicableSpecs = specs.filter(
    (spec) => spec.appliesTo.some((type) => targetTypes.includes(type)) || isActive(spec.effectId),
  );
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

  const setParams = async (spec: EffectSpec, params: Record<string, unknown>): Promise<boolean> => {
    if (busy) return false;
    setBusy(true);
    const ctx = getLatestState() || state;
    const result = await updateEffect(dispatch, ctx, {
      clipId: clip.id, effectId: spec.effectId, params,
    });
    setBusy(false);
    return result.ok;
  };

  const Icon = iconForCategory("fx");
  const activeSpecs = applicableSpecs.filter((s) => isActive(s.effectId));
  const sectionLabel = trackKind === "audio" ? "音频特效" : mediaKind === "image" ? "画面特效" : "片段特效";

  return (
    <>
      <div className="inspector__sep" />
      <details className="inspector__adv inspector__fx-picker">
        <summary style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <span className="inspector__key">
            <SlidersHorizontal size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            {sectionLabel}
          </span>
          <span className="cv-hint">
            {applicableSpecs.length ? `${activeSpecs.length}/${applicableSpecs.length} 已启用` : "展开添加"}
          </span>
        </summary>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {applicableSpecs.map((s) => {
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
      </details>
      {activeSpecs.map((s) => (
        <FxParams
          key={s.effectId}
          spec={s}
          params={paramsOf(s.effectId)}
          onCommit={(p) => setParams(s, p)}
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
  onCommit: (next: Record<string, unknown>) => Promise<boolean>;
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
      onCommit={(change) => {
        const next = { ...local };
        if (change) {
          if (change.value === undefined) delete next[change.name];
          else next[change.name] = change.value;
        }
        if (JSON.stringify(next) === JSON.stringify(params)) return;
        void onCommit(next).then((ok) => {
          if (!ok) setLocal(params);
        });
      }}
    />
  );
}
