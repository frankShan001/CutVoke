/** 效果参数 → 可渲染控件描述（J01，自 lib/effects.ts 拆出保持 ≤300 行）。 */

import type { EffectSpec } from "./effects";

export type ParamKind = "number" | "enum" | "boolean" | "string";

export interface ParamControl {
  name: string;
  label: string;
  kind: ParamKind;
  value: unknown;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  options?: { value: string; label: string }[];
}

const PARAM_LABELS: Record<string, string> = {
  duration: "时长",
  easing: "缓动",
  sigma: "强度",
  angle: "角度",
  distance: "位移",
  fromScale: "起始缩放",
  toScale: "结束缩放",
  fromAngle: "起始角度",
  overshoot: "过冲",
  direction: "方向",
  amplitude: "幅度",
  period: "周期",
  intensity: "强度",
  offset: "偏移",
  amount: "强度",
  levels: "色阶",
  mode: "模式",
  brightness: "亮度",
  contrast: "对比度",
  saturation: "饱和度",
  opacity: "不透明度",
  scale: "缩放",
  rotation: "旋转",
};

function localizedDescriptor(v: unknown): string {
  if (typeof v === "string") return v;
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    const hit = o["zh-CN"] ?? o["zh"] ?? o["en"];
    if (typeof hit === "string") return hit;
    const first = Object.values(o).find((x) => typeof x === "string");
    if (typeof first === "string") return first;
  }
  return "";
}

function paramLabel(name: string, sub: EffectParamSpecLike): string {
  return localizedDescriptor(sub.description) || PARAM_LABELS[name] || name;
}

interface EffectParamSpecLike {
  type?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: unknown[];
  unit?: string;
  description?: unknown;
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
      kind,
      value,
      unit: typeof sub.unit === "string" ? sub.unit : undefined,
    };
    if (kind === "number") {
      if (typeof sub.minimum === "number") ctrl.min = sub.minimum;
      if (typeof sub.maximum === "number") ctrl.max = sub.maximum;
      ctrl.step = t === "integer" ? 1 : 0.05;
    }
    if (kind === "enum") {
      ctrl.options = (sub.enum || []).map((v) => ({ value: String(v), label: String(v) }));
    }
    out.push(ctrl);
  }
  return out;
}

function formatValue(v: unknown): string {
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  if (typeof v === "boolean") return v ? "开" : "关";
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