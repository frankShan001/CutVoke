import { tr, useLocale } from "../../i18n";
/** Four independent animation lanes share the registry, project and renderer. */
import { useEffect, useMemo, useState } from "react";
import { Sparkles } from "lucide-react";
import { showError, useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { stageEffect, releaseEdit } from "../../store/interactive";
import { applyAnimation } from "../../store/clipEdit";
import {
  effectsByCategory,
  getEffectCatalog,
  type EffectSpec,
} from "../../lib/effects";
import { paramControls } from "../../lib/effectControls";
import { ParamControls } from "./ParamControls";
import type { Clip } from "../../types/api";
import { inferKind } from "../../lib/assetStore";
import { subcategoryLabel } from "../../i18n/resourceLabels";

const ANIM_PREFIX = "cutvoke.anim.";
const SLOTS = ["入场", "出场", "循环", "组合"] as const;
type Slot = (typeof SLOTS)[number];
type Draft = { id: string; params: Record<string, unknown> };
type KeyframePolicy = "combine" | "replace";
type PendingConflict = { slot: Slot; id: string; params: Record<string, unknown> };
const emptyDraft = (): Draft => ({ id: "", params: {} });

function animationAffectsOpacity(id: string, params: Record<string, unknown>): boolean {
  if ([
    "cutvoke.anim.fadeIn", "cutvoke.anim.fadeOut", "cutvoke.anim.rotateIn",
    "cutvoke.anim.rotateOut", "cutvoke.anim.backIn", "cutvoke.anim.blink",
  ].includes(id)) return true;
  if (!id.startsWith("cutvoke.anim.combo")) return false;
  return params.primary === "fadeIn" || params.secondary === "fadeIn";
}

export function AnimationSection({ clip }: { clip: Clip }) {
  const locale = useLocale();
  const { state, dispatch } = useEditor();
  const isImage = inferKind(clip.assetRef.sourcePath, false, false) === "image";
  const visibleSlots = isImage ? SLOTS.filter((slot) => slot !== "组合") : SLOTS;
  const [specs, setSpecs] = useState<EffectSpec[]>([]);
  const [drafts, setDrafts] = useState<Record<Slot, Draft>>(() => ({
    入场: emptyDraft(), 出场: emptyDraft(), 循环: emptyDraft(), 组合: emptyDraft(),
  }));
  const [pendingConflict, setPendingConflict] = useState<PendingConflict | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getEffectCatalog()
      .then((all) => {
        if (!cancelled) setSpecs(effectsByCategory(all, "animation"));
      })
      .catch((err) => {
        if (!cancelled) showError(dispatch, err);
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch, locale]);

  const specsBySlot = useMemo(() => Object.fromEntries(
    SLOTS.map((slot) => [slot, specs.filter((spec) => spec.subcategory === slot)]),
  ) as Record<Slot, EffectSpec[]>, [specs]);
  const specIds = specs.map(spec => spec.effectId).join("\u0000");

  useEffect(() => {
    const next = {} as Record<Slot, Draft>;
    for (const slot of SLOTS) {
      const existing = clip.effects?.find((effect) =>
        String(effect.effectId).startsWith(ANIM_PREFIX)
        && specsBySlot[slot].some((spec) => spec.effectId === effect.effectId));
      next[slot] = existing
        ? { id: String(existing.effectId), params: { ...(existing.params || {}) } }
        : emptyDraft();
    }
    setDrafts(next);
    setBusy(false);
  }, [clip.id, clip.effects, specIds]);

  const apply = async (
    slot: Slot,
    nextId: string,
    nextParams: Record<string, unknown>,
    keyframePolicy: KeyframePolicy = "combine",
  ) => {
    if (busy) return;
    setBusy(true);
    const previous = drafts[slot];
    const ctx = getLatestState() || state;
    const res = await applyAnimation(dispatch, ctx, {
      clipId: clip.id,
      animationId: nextId,
      duration: 1,
      params: nextParams,
      slot,
      keyframePolicy,
    });
    if (res.ok) {
      setDrafts((current) => ({ ...current, [slot]: { id: nextId, params: nextParams } }));
    } else {
      setDrafts((current) => ({ ...current, [slot]: previous }));
    }
    setBusy(false);
  };

  const pick = (slot: Slot, nextId: string) => {
    const nextSpec = specsBySlot[slot].find((spec) => spec.effectId === nextId);
    const nextParams = nextSpec ? { ...nextSpec.defaults } : {};
    if (clip.keyframes?.opacity?.length
        && animationAffectsOpacity(nextId, nextParams)) {
      setPendingConflict({ slot, id: nextId, params: nextParams });
      return;
    }
    void apply(slot, nextId, nextParams);
  };

  const changeParam = (slot: Slot, name: string, value: unknown) => {
    const draft = drafts[slot], params = { ...draft.params, [name]: value };
    setDrafts(current => ({ ...current, [slot]: { ...draft, params } }));
    const control = specsBySlot[slot].find(s => s.effectId === draft.id);
    const schema = control?.parameters.properties?.[name];
    if (value !== undefined && (typeof value !== "number" || Number.isFinite(value) && (schema?.minimum === undefined || value >= Number(schema.minimum)) && (schema?.maximum === undefined || value <= Number(schema.maximum))))
      stageEffect(dispatch, clip, draft.id, params);
  };

  return (
    <>
      <div className="inspector__sep" />
      <div className="inspector__subrow">
        <span className="inspector__key">
          <Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          {isImage ? tr("图片动画") : tr("视频动画")}
        </span>
      </div>
      {visibleSlots.map((slot) => {
        const draft = drafts[slot];
        const spec = specsBySlot[slot].find((item) => item.effectId === draft.id);
        const controls = spec ? paramControls(spec, draft.params) : [];
        return (
          <div key={slot} className="inspector__animation-lane" data-effect-id={draft.id || undefined}>
            <label className="inspector__key" htmlFor={`animation-${slot}`}>{subcategoryLabel(slot)}</label>
            <select
              id={`animation-${slot}`}
              value={draft.id}
              aria-label={tr("{0}动画", subcategoryLabel(slot))}
              className="cv-input"
              style={{ height: 28, width: "100%" }}
              disabled={busy}
              onChange={(event) => pick(slot, event.target.value)}
            >
              <option value="">{tr("无")}</option>
              {specsBySlot[slot].map((item) => (
                <option key={item.effectId} value={item.effectId}>{item.name}</option>
              ))}
            </select>
            {draft.id && spec ? (
              <ParamControls
                controls={controls}
                onChange={(name, value) => changeParam(slot, name, value)}
                onCommit={() => releaseEdit(`${clip.id}:${draft.id}`)}
              />
            ) : null}
          </div>
        );
      })}
      {pendingConflict ? (
        <div
          role="alertdialog"
          aria-label={tr("动画与关键帧冲突选择")}
          className="inspector__animation-conflict"
          style={{
            display: "grid", gap: 8, marginTop: 8, padding: 10,
            border: "1px solid var(--border-strong)", borderRadius: 6,
            background: "var(--bg-2)",
          }}
        >
          <strong>{tr("该动画会改变透明度，片段已有手动透明度关键帧。")}</strong>
          <span className="cv-hint">{tr("保留关键帧会与动画叠加；移除会清除该片段全部透明度关键帧。")}</span>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button
              type="button"
              className="cv-btn cv-btn--sm cv-btn--secondary"
              disabled={busy}
              onClick={() => {
                const pending = pendingConflict;
                setPendingConflict(null);
                void apply(pending.slot, pending.id, pending.params, "combine");
              }}
            >{tr("保留关键帧并叠加")}</button>
            <button
              type="button"
              className="cv-btn cv-btn--sm cv-btn--danger"
              disabled={busy}
              onClick={() => {
                const pending = pendingConflict;
                setPendingConflict(null);
                void apply(pending.slot, pending.id, pending.params, "replace");
              }}
            >{tr("移除关键帧后应用")}</button>
            <button
              type="button"
              className="cv-btn cv-btn--sm cv-btn--ghost"
              disabled={busy}
              onClick={() => setPendingConflict(null)}
            >{tr("取消")}</button>
          </div>
        </div>
      ) : null}
      <p className="cv-hint" style={{ marginTop: 4 }}>
        {isImage
          ? tr("图片可分别设置入场、出场和循环动画。")
          : tr("视频可分别设置入场、出场、循环和组合动画；每类切换只替换同类动画。")}
      </p>
    </>
  );
}
