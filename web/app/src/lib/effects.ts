/** 统一资源客户端（J01）：效果注册表 / 工程资源 / 个人预设。
 * 前端不持有第二份效果清单——分类、名称、参数 schema 一律从后端拉取。
 * 后端字段是 effectId（非 id），name/description 按 lang 本地化；本模块归一化。 */
import type { LucideIcon } from "lucide-react";
import { ArrowRightLeft, Sparkles, Wand2, Palette, Move, Shapes } from "lucide-react";
import { ApiFailure, API_BASE } from "./api";

/** 效果分类（对应后端 registry category 常量）。 */
export type EffectCategory = "transition" | "animation" | "fx" | "color" | "transform";

export interface EffectParamSpec {
  type?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  enum?: unknown[];
  unit?: string;
  description?: unknown;
  properties?: Record<string, EffectParamSpec>;
  items?: EffectParamSpec;
  [k: string]: unknown;
}

export interface EffectParameters {
  type?: string;
  properties?: Record<string, EffectParamSpec>;
  additionalProperties?: boolean;
}

export interface EffectSpec {
  effectId: string;
  version: string;
  name: string;
  category: EffectCategory;
  description: string;
  keywords: string[];
  appliesTo: string[];
  parameters: EffectParameters;
  defaults: Record<string, unknown>;
  animatable: string[];
  preview: string;
  source: string;
  dependencies: string[];
}

export interface ProjectResource extends EffectSpec {
  favorite: boolean;
  recent: boolean;
}

export interface PresetEffect {
  effectId: string;
  params: Record<string, unknown>;
}

export interface Preset {
  id: string;
  name: string;
  effects: PresetEffect[];
}

export interface ProjectResources {
  effects: ProjectResource[];
  favorites: string[];
  recent: string[];
}

export interface ResourceQuery {
  q?: string;
  category?: EffectCategory | "";
  appliesTo?: string;
  favoritesOnly?: boolean;
  recentOnly?: boolean;
}

// ---------------------------------------------------------------------------
// 底层请求
// ---------------------------------------------------------------------------
async function getJson<T>(path: string, params?: Record<string, string | undefined>): Promise<T> {
  const qs = new URLSearchParams();
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") qs.set(k, v);
    }
  }
  const q = qs.toString();
  const url = API_BASE + path + (q ? `?${q}` : "");
  let res: Response;
  try {
    res = await fetch(url, { headers: { Accept: "application/json" } });
  } catch (err) {
    throw new ApiFailure({
      status: 0,
      code: "NETWORK",
      message: err instanceof Error ? err.message : "无法连接后端服务",
      retryable: true,
      path: url,
    });
  }
  const data = await res.json().catch(() => undefined);
  if (!res.ok) {
    type ErrBody = { code?: string; message?: string; details?: unknown; retryable?: boolean };
    const e = (data as { error?: ErrBody } | undefined)?.error;
    throw new ApiFailure({
      status: res.status,
      code: e?.code || `HTTP_${res.status}`,
      message: e?.message || `请求失败（HTTP ${res.status}）`,
      details: e?.details,
      retryable: e?.retryable,
      path: url,
    });
  }
  return data as T;
}

// ---------------------------------------------------------------------------
// 归一化
// ---------------------------------------------------------------------------
/** 把 name/description 的两种形态（string / {zh-CN,en}）收敛成字符串。 */
function localized(v: unknown): string {
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

function strArr(v: unknown, fallback: string[] = []): string[] {
  return Array.isArray(v) ? v.map((x) => String(x)) : fallback;
}

function normalizeEffect(raw: unknown): EffectSpec {
  const r = (raw || {}) as Record<string, unknown>;
  const id = String(r.effectId ?? r.id ?? "");
  if (!id) {
    throw new ApiFailure({
      status: 500,
      code: "BAD_EFFECT_PAYLOAD",
      message: "后端返回的效果缺少 effectId",
    });
  }
  return {
    effectId: id,
    version: String(r.version ?? ""),
    name: localized(r.name) || id,
    category: (r.category as EffectCategory) ?? "fx",
    description: localized(r.description),
    keywords: strArr(r.keywords),
    appliesTo: strArr(r.appliesTo, ["video"]),
    parameters: (r.parameters as EffectParameters) ?? {},
    defaults: (r.defaults as Record<string, unknown>) ?? {},
    animatable: strArr(r.animatable),
    preview: typeof r.preview === "string" ? r.preview : "",
    source: typeof r.source === "string" ? r.source : "",
    dependencies: strArr(r.dependencies),
  };
}

function normalizePreset(raw: unknown): Preset {
  const r = (raw || {}) as Record<string, unknown>;
  const effects = Array.isArray(r.effects) ? r.effects : [];
  return {
    id: String(r.id ?? r.presetId ?? ""),
    name: String(r.name ?? ""),
    effects: effects
      .map((e) => {
        const o = (e || {}) as Record<string, unknown>;
        return {
          effectId: String(o.effectId ?? o.id ?? ""),
          params: (o.params as Record<string, unknown>) ?? {},
        };
      })
      .filter((e) => e.effectId),
  };
}

// ---------------------------------------------------------------------------
// 公共 API
// ---------------------------------------------------------------------------
export async function listEffects(opts: ResourceQuery = {}): Promise<EffectSpec[]> {
  const data = await getJson<{ effects?: unknown[] }>("/effects", {
    q: opts.q,
    category: opts.category,
    appliesTo: opts.appliesTo,
  });
  return (data.effects || []).map(normalizeEffect);
}

export async function listProjectResources(
  projectId: string,
  opts: ResourceQuery = {},
): Promise<ProjectResources> {
  const data = await getJson<{ effects?: unknown[]; favorites?: unknown; recent?: unknown }>(
    `/projects/${encodeURIComponent(projectId)}/resources`,
    {
      q: opts.q,
      category: opts.category,
      appliesTo: opts.appliesTo,
      favoritesOnly: opts.favoritesOnly ? "1" : undefined,
      recentOnly: opts.recentOnly ? "1" : undefined,
    },
  );
  return {
    effects: (data.effects || []).map((e) => {
      const spec = normalizeEffect(e);
      const r = (e || {}) as Record<string, unknown>;
      return { ...spec, favorite: !!r.favorite, recent: !!r.recent };
    }),
    favorites: strArr(data.favorites),
    recent: strArr(data.recent),
  };
}

export async function listPresets(projectId: string): Promise<Preset[]> {
  const data = await getJson<{ presets?: unknown[] }>(
    `/projects/${encodeURIComponent(projectId)}/presets`,
  );
  return (data.presets || []).map(normalizePreset);
}

// ---------------------------------------------------------------------------
// 分类派生（tab / 文案 / 图标）—— 从 category 枚举派生，不写死效果 ID
// ---------------------------------------------------------------------------
export const CATEGORY_ORDER: EffectCategory[] = [
  "transition",
  "animation",
  "fx",
  "color",
  "transform",
];

const CATEGORY_LABELS: Record<string, string> = {
  transition: "转场",
  animation: "动画",
  fx: "画面特效",
  color: "调色",
  transform: "基础",
};

export function categoryLabelOf(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

export function iconForCategory(category: string): LucideIcon {
  switch (category) {
    case "transition":
      return ArrowRightLeft;
    case "animation":
      return Sparkles;
    case "fx":
      return Wand2;
    case "color":
      return Palette;
    case "transform":
      return Move;
    default:
      return Shapes;
  }
}

/** 分类集合 → 稳定顺序（已知分类按 CATEGORY_ORDER，未知分类排在末尾）。 */
export function sortCategories(cats: Iterable<string>): string[] {
  const set = new Set(cats);
  const known = CATEGORY_ORDER.filter((c) => set.has(c));
  const extra = [...set]
    .filter((c) => !(CATEGORY_ORDER as string[]).includes(c))
    .sort();
  return [...known, ...extra];
}

// ---------------------------------------------------------------------------
// 注册表缓存 / 查询
// ---------------------------------------------------------------------------
let _catalog: EffectSpec[] | null = null;

/** 拉取（并缓存）全量效果注册表；force=true 强制刷新。 */
export async function getEffectCatalog(force = false): Promise<EffectSpec[]> {
  if (_catalog && !force) return _catalog;
  _catalog = await listEffects();
  return _catalog;
}

export function effectMap(specs: EffectSpec[]): Map<string, EffectSpec> {
  return new Map(specs.map((s) => [s.effectId, s]));
}

export function effectsByCategory(specs: EffectSpec[], category: string): EffectSpec[] {
  return specs.filter((s) => s.category === category);
}

/** 显示名：注册表有则用中文名，否则回退 effectId（绝不空白）。 */
export function effectLabel(specs: EffectSpec[], effectId: string): string {
  return specs.find((s) => s.effectId === effectId)?.name || effectId;
}

// ---------------------------------------------------------------------------
// 内置效果 ID 单一来源（消除 components 内散落的硬编码字面量）
// 这些「基础/核心」效果恒存在；快捷入口（右键菜单、Inspector 芯片）只引用此处常量，
// 不再把 effectId 字符串写死在多处。转场/动画/特效的完整清单仍从注册表派生
// （见 TransitionPanel / effectStack 等），本常量仅服务少数固定快捷按钮。
// ---------------------------------------------------------------------------
export const BUILTIN_TRANSFORM_ID = "cutvoke.transform";
export const BUILTIN_COLOR_ID = "cutvoke.color";
export const BUILTIN_CROSSFADE_ID = "cutvoke.transition.crossfade";
