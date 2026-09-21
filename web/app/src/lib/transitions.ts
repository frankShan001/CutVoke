/** 转场纯函数工具。
 *
 * 转场语义：挂在「后一段」片段上，做它与前一段之间的 xfade。
 * 检测片段上的转场：clip.effects 里 effectId 前缀为 cutvoke.transition. 的实例。
 *
 * J01：不再持有硬编码转场清单（历史上曾与后端漂移——含一个后端已删除的旧转场）。
 * 转场类型 / 名称 / 参数一律从 /api/v1/effects?category=transition 派生，见 lib/effects.ts。
 * 本文件只保留按前缀判断的纯函数（安全，与注册表无关）。 */

import type { Clip, EffectInstance } from "../types/api";

export function isTransitionEffectId(effectId: string): boolean {
  return effectId.startsWith("cutvoke.transition.");
}

/** 片段当前生效的转场实例（取效果栈里第一个转场）。无则 null。 */
export function findTransitionOnClip(clip: Clip | null | undefined): EffectInstance | null {
  if (!clip || !Array.isArray(clip.effects)) return null;
  const fx = clip.effects.find((e) => e && isTransitionEffectId(String(e.effectId)));
  return fx || null;
}

/** 转场时长（秒），非法/缺失回退默认 1.0。 */
export function transitionDurationSecs(fx: EffectInstance | null): number {
  if (!fx || !fx.params || typeof fx.params.duration !== "number") return 1.0;
  return fx.params.duration;
}

/** 默认参数。 */
export function defaultTransitionParams(duration: number): Record<string, unknown> {
  return { duration };
}
