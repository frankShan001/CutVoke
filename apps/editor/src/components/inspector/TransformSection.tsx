import { tr, useLocale } from "../../i18n";
/** 数值变换：与画布拖动共用 cutvoke.transform 参数。 */
import { useEffect, useState } from "react";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { addKeyframe, removeKeyframe } from "../../store/clipEdit";
import { stageTransform } from "../../store/interactive";
import { BUILTIN_TRANSFORM_ID } from "../../lib/effects";
import { evaluateKeyframes } from "../../lib/keyframes";
import { rationalToSecs } from "../../lib/rational";
import type { Clip } from "../../types/api";

type TransformValues = { x: number; y: number; scale: number; rotation: number; opacity: number };
const DEFAULTS: TransformValues = { x: 0, y: 0, scale: 1, rotation: 0, opacity: 1 };
const TRANSFORM_KEYFRAME_PARAMS = ["x", "y", "scale", "rotation"] as const;
type TransformKeyframeParam = typeof TRANSFORM_KEYFRAME_PARAMS[number];
const TRANSFORM_LABELS: Record<TransformKeyframeParam, string> = {
  get x() { return tr("水平位置 X"); }, get y() { return tr("垂直位置 Y"); }, get scale() { return tr("缩放"); }, get rotation() { return tr("旋转"); },
};

function readTransform(clip: Clip, localTime: number): TransformValues {
  const params = clip.effects?.find((effect) => effect.effectId === BUILTIN_TRANSFORM_ID)?.params;
  const position = params?.position as { x?: number; y?: number } | undefined;
  const base: TransformValues = {
    x: Number(position?.x ?? DEFAULTS.x),
    y: Number(position?.y ?? DEFAULTS.y),
    scale: Number(params?.scale ?? DEFAULTS.scale),
    rotation: Number(params?.rotation ?? DEFAULTS.rotation),
    opacity: Number(params?.opacity ?? DEFAULTS.opacity),
  };
  return {
    x: Math.round(evaluateKeyframes(clip.keyframes?.x, localTime, base.x)),
    y: Math.round(evaluateKeyframes(clip.keyframes?.y, localTime, base.y)),
    scale: evaluateKeyframes(clip.keyframes?.scale, localTime, base.scale),
    rotation: evaluateKeyframes(clip.keyframes?.rotation, localTime, base.rotation),
    opacity: evaluateKeyframes(clip.keyframes?.opacity, localTime, base.opacity),
  };
}

export function TransformSection({ clip }: { clip: Clip }) {
  useLocale();
  const { state, dispatch } = useEditor();
  const clipStart = rationalToSecs(clip.timelineStart);
  const clipEnd = rationalToSecs(clip.timelineEnd);
  const keyframeTime = state.playhead - clipStart;
  const saved = readTransform(clip, keyframeTime);
  const savedKey = JSON.stringify(saved);
  const [values, setValues] = useState(saved);
  const [saving, setSaving] = useState(false);
  const opacityKeyframeCount = clip.keyframes?.opacity?.length ?? 0;
  const playheadInsideClip = state.playhead >= clipStart - 1e-4 && state.playhead <= clipEnd + 1e-4;
  useEffect(() => setValues(JSON.parse(savedKey) as TransformValues), [clip.id, savedKey]);

  const isValid = (candidate: TransformValues) => Object.values(candidate).every(Number.isFinite) &&
    candidate.scale >= 0.05 && candidate.scale <= 5 &&
    candidate.rotation >= -180 && candidate.rotation <= 180 &&
    candidate.opacity >= 0 && candidate.opacity <= 1;
  const valid = isValid(values);
  const keyframeDisabledReason = !valid
    ? tr("变换数值超出可用范围，修正后才能应用或记录关键帧。")
    : !playheadInsideClip
      ? tr("播放头位于片段外；移到 {0}–{1} 秒范围内即可记录关键帧。", clipStart.toFixed(2), clipEnd.toFixed(2))
      : "";
  const keyframeDisabledReasonId = keyframeDisabledReason
    ? `transform-keyframe-disabled-${clip.id}` : undefined;
  const commit = (next: TransformValues) => {
    if (isValid(next)) stageTransform(dispatch, clip, next, Math.max(0, keyframeTime));
  };
  const update = (key: keyof TransformValues, value: string) => {
    const number = value === "" ? Number.NaN : Number(value);
    const next = { ...values, [key]: number };
    setValues(next); commit(next);
  };

  const recordTransformKeyframe = async (param: TransformKeyframeParam) => {
    if (!valid || saving || !playheadInsideClip) return;
    setSaving(true);
    try {
      const ctx = getLatestState() || state;
      await addKeyframe(dispatch, ctx, {
        clipId: clip.id, param, time: Math.max(0, keyframeTime),
        value: values[param], interpolation: "linear",
      });
    } finally {
      setSaving(false);
    }
  };

  const deleteTransformKeyframe = async (param: TransformKeyframeParam, keyframeId: string) => {
    if (saving) return;
    setSaving(true);
    try {
      const ctx = getLatestState() || state;
      await removeKeyframe(dispatch, ctx, { clipId: clip.id, param, keyframeId });
    } finally {
      setSaving(false);
    }
  };

  const fields: { key: keyof TransformValues; label: string; step: number; min?: number; max?: number; unit: string }[] = [
    { key: "x", label: tr("水平位置 X"), step: 1, unit: tr("像素") },
    { key: "y", label: tr("垂直位置 Y"), step: 1, unit: tr("像素") },
    { key: "scale", label: tr("缩放"), step: 0.01, min: 0.05, max: 5, unit: "×" },
    { key: "rotation", label: tr("旋转"), step: 1, min: -180, max: 180, unit: "°" },
    { key: "opacity", label: tr("不透明度"), step: 0.01, min: 0, max: 1, unit: "%" },
  ];

  return (
    <section className="inspector__transform" data-effect-id={BUILTIN_TRANSFORM_ID} aria-label={tr("画面变换")}>
      <div className="inspector__subrow"><strong>{tr("画面变换")}</strong>
        <span className="cv-hint">{tr("画布拖动与数值同步")}</span>
      </div>
      {keyframeDisabledReason ? <p id={keyframeDisabledReasonId} className="cv-hint" role="status">
        {keyframeDisabledReason}
      </p> : null}
      {fields.map((field) => (
        <label key={field.key} className="inspector__subrow">
          <span className="inspector__key">{field.label}</span>
          <input
            type="number" className="cv-input" aria-label={field.label}
            step={field.step} min={field.min} max={field.max}
            value={Number.isFinite(values[field.key]) ? values[field.key] : ""}
            onChange={(event) => update(field.key, event.target.value)}
            style={{ width: 86, height: 26 }}
          />
          <span className="inspector__unit">{field.key === "opacity" ? `${Math.round(values.opacity * 100)}%` : field.unit}</span>
          {field.key !== "opacity" && (
            <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
              aria-label={tr("记录{0}关键帧", field.label)}
              aria-describedby={keyframeDisabledReasonId}
              title={keyframeDisabledReason || tr("在播放头记录{0}关键帧", field.label)}
              disabled={saving || !valid || !playheadInsideClip}
              onClick={() => void recordTransformKeyframe(field.key as TransformKeyframeParam)}
              style={{ width: 28, minWidth: 28, padding: 0 }}>
              +
            </button>
          )}
        </label>
      ))}
      <div className="inspector__subrow" aria-label={tr("画面变换关键帧状态")}>
        <span className="inspector__key">{tr("关键帧状态")}</span>
        <span className="cv-hint" aria-live="polite">{tr("不透明度 ")}{opacityKeyframeCount}{tr(" 个 · ")}{TRANSFORM_KEYFRAME_PARAMS.map((param) =>
            tr("{0} {1} 个", param.toUpperCase(), clip.keyframes?.[param]?.length ?? 0)).join(" · ")}
        </span>
        <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
          aria-label={tr("编辑不透明度关键帧")}
          onClick={() => {
            const editor = document.getElementById("clip-opacity-keyframes");
            editor?.scrollIntoView({ behavior: "smooth", block: "nearest" });
            editor?.focus({ preventScroll: true });
          }}>{tr("编辑")}</button>
      </div>
      {TRANSFORM_KEYFRAME_PARAMS.map((param) => {
        const keyframes = [...(clip.keyframes?.[param] ?? [])]
          .sort((left, right) => rationalToSecs(left.time) - rationalToSecs(right.time));
        if (keyframes.length === 0) return null;
        return (
          <div key={param} aria-label={tr("{0}关键帧", TRANSFORM_LABELS[param])}>
            {keyframes.map((keyframe) => {
              const time = rationalToSecs(keyframe.time);
              const numericValue = Number(keyframe.value);
              const value = param === "x" || param === "y"
                ? Math.round(numericValue).toString() : numericValue.toFixed(2);
              return (
                <div key={keyframe.id} className="inspector__subrow" style={{ gap: 6 }}>
                  <span className="inspector__key">{TRANSFORM_LABELS[param]}</span>
                  <span className="cv-mono">t={time.toFixed(2)}s</span>
                  <span className="inspector__val">{value}{param === "scale" ? "×" : param === "rotation" ? "°" : "px"}</span>
                  <button type="button" className="cv-btn cv-btn--sm cv-btn--danger"
                    aria-label={tr("删除{0}关键帧 t={1}s", TRANSFORM_LABELS[param], time.toFixed(2))}
                    disabled={saving}
                    onClick={() => void deleteTransformKeyframe(param, keyframe.id)}
                    style={{ marginLeft: "auto" }}>{tr("删除")}</button>
                </div>
              );
            })}
          </div>
        );
      })}
      <p className="cv-hint">{tr("在播放头记录 X/Y、缩放或旋转值；片段内按关键帧曲线插值，未设置关键帧时全片使用当前变换值。")}</p>
      <div className="inspector__subrow">
        <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
          disabled={saving || JSON.stringify(values) === JSON.stringify(DEFAULTS)}
          onClick={() => { setValues(DEFAULTS); void commit(DEFAULTS); }}>{tr("复位")}</button>
      </div>
      <p className="cv-hint">{tr("位置以画布中心为原点；修改即时预览并自动保存，一次调整可以一步撤销。")}</p>
    </section>
  );
}
