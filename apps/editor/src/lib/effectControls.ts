import { tr } from "../i18n";
import { getLocale } from "../i18n/locale";
/** 效果参数 → 可渲染控件描述（J01，自 lib/effects.ts 拆出保持 ≤300 行）。 */

import type { EffectSpec } from "./effects";

export type ParamKind = "number" | "enum" | "boolean" | "string";

export interface ParamControl {
  name: string;
  label: string;
  description?: string;
  kind: ParamKind;
  value: unknown;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  options?: { value: string; label: string }[];
}

const PARAM_LABELS: Record<string, string> = {
  // Axis names stay stable when richer schema help is added.
  x: "x", y: "y", z: "z",
  get time() { return tr('关键帧时间（秒）'); },
  get at() { return tr('进度'); },
  get value() { return tr('数值'); },
  get width() { return tr('宽度'); },
  get height() { return tr('高度'); },
  get feather() { return tr('羽化'); },
  get duration() { return tr("时长"); },
  get easing() { return tr("缓动"); },
  get sigma() { return tr("强度"); },
  get angle() { return tr("角度"); },
  get distance() { return tr("位移"); },
  get fromScale() { return tr("起始缩放"); },
  get toScale() { return tr("结束缩放"); },
  get fromAngle() { return tr("起始角度"); },
  get overshoot() { return tr("过冲"); },
  get direction() { return tr("方向"); },
  get amplitude() { return tr("幅度"); },
  get period() { return tr("周期"); },
  get intensity() { return tr("强度"); },
  get strength() { return tr("强度"); },
  get low() { return tr("低阈值"); },
  get high() { return tr("高阈值"); },
  get offset() { return tr("偏移"); },
  get amount() { return tr("强度"); },
  get levels() { return tr("色阶"); },
  get mode() { return tr("模式"); },
  get axis() { return tr("翻转轴向"); },
  get brightness() { return tr("亮度"); },
  get contrast() { return tr("对比度"); },
  get saturation() { return tr("饱和度"); },
  get opacity() { return tr("不透明度"); },
  get scale() { return tr("缩放"); },
  get rotation() { return tr("旋转"); },
  get content() { return tr("蒙版文字"); },
  get fontSize() { return tr("文字大小"); },
};

const PARAM_ENUM_LABELS: Record<string, Record<string, string>> = {
  axis: { get horizontal() { return tr("水平"); }, get vertical() { return tr("垂直"); }, get both() { return tr("水平 + 垂直"); } },
};

function localizedDescriptor(v: unknown): string {
  if (typeof v === "string") return v;
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    const language = getLocale();
    const hit = o[language] ?? o[language.split("-")[0]] ?? o.en ?? o["zh-CN"];
    if (typeof hit === "string") return hit;
    const first = Object.values(o).find((x) => typeof x === "string");
    if (typeof first === "string") return first;
  }
  return "";
}

function paramLabel(name: string, sub: EffectParamSpecLike): string {
  const description = localizedDescriptor(sub.description);
  return localizedDescriptor(sub["x-cutvoke-label"]) || PARAM_LABELS[name] ||
    description.split(/[；。:：]/, 1)[0].slice(0, 12) || name;
}

interface EffectParamSpecLike {
  type?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: unknown[];
  unit?: string;
  description?: unknown;
  "x-cutvoke-enum-labels"?: Record<string, unknown>;
  "x-cutvoke-label"?: unknown;
  "x-cutvoke-step"?: number;
}

/** 把 effect 的 parameters 顶层属性转成控件描述（嵌套 object/array 跳过，交调用方处理）。 */
export function paramControls(
  spec: EffectSpec,
  params?: Record<string, unknown>,
): ParamControl[] {
  const props = (spec.parameters?.properties as Record<string, EffectParamSpecLike> | undefined) || {};
  const current = params || {};
  const out: ParamControl[] = [];
  for (const [name, raw] of Object.entries(props)) {
    if (!raw || typeof raw !== "object") continue;
    const sub = raw as EffectParamSpecLike;
    const t = sub.type;
    if (t === "object" || t === "array") continue;
    const value = name in current ? current[name] : sub.default;
    const kind: ParamKind =
      t === "boolean"
        ? "boolean"
        : Array.isArray(sub.enum)
          ? "enum"
          : t === "number" || t === "integer"
            ? "number"
            : "string";
    const ctrl: ParamControl = {
      name,
      label: paramLabel(name, sub),
      description: localizedDescriptor(sub.description),
      kind,
      value,
      unit: typeof sub.unit === "string" ? sub.unit : undefined,
    };
    if (kind === "number") {
      if (typeof sub.minimum === "number") ctrl.min = sub.minimum;
      if (typeof sub.maximum === "number") ctrl.max = sub.maximum;
      ctrl.step = t === "integer" ? 1 :
        typeof sub["x-cutvoke-step"] === "number" && sub["x-cutvoke-step"] > 0
          ? sub["x-cutvoke-step"] : 0.05;
    }
    if (kind === "enum") {
      ctrl.options = (sub.enum || []).map((v) => {
        const value = String(v);
        return {
          value,
          label: PARAM_ENUM_LABELS[name]?.[value] ??
            (localizedDescriptor(sub["x-cutvoke-enum-labels"]?.[value]) || value),
        };
      });
    }
    out.push(ctrl);
  }
  return out;
}

function formatValue(v: unknown): string {
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  if (typeof v === "boolean") return v ? tr("开") : tr("关");
  return String(v ?? "");
}

/** 一行参数摘要（供效果栈行 / 卡片显示关键参数）。 */
export function paramsSummary(
  spec: EffectSpec | undefined,
  params: Record<string, unknown> | undefined,
): string {
  if (!spec) return "";
  return paramControls(spec, params)
    .filter((c) => c.value !== undefined)
    .slice(0, 3)
    .map((c) => `${c.label} ${formatValue(c.value)}${c.unit ?? ""}`)
    .join(" · ");
}
