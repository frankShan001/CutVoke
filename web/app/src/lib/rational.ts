/** 有理数时间工具链。
    CutVoke 权威时间一律 {num,den} 十进制字符串；禁止浮点秒参与提交。
    秒输入仅用于 UI 表单，提交前经 secsToRational 精确转换。 */

import type { Rational } from "../types/api";

function gcd(a: number, b: number): number {
  a = Math.abs(a);
  b = Math.abs(b);
  while (b) {
    const t = b;
    b = a % b;
    a = t;
  }
  return a || 1;
}

/** 验证并转换秒输入（字符串/数字）为约分后的有理数。抛错带友好消息。 */
export function secsToRational(input: string | number): Rational {
  const s = String(input).trim();
  if (s === "") throw new Error("时间不能为空");
  if (!/^-?\d+(\.\d+)?$/.test(s)) {
    throw new Error(`时间必须为数字（秒），例如 3.5 / 10 / 0.0333333`);
  }
  if (!s.includes(".")) {
    return { num: s, den: "1" };
  }
  const neg = s.startsWith("-");
  const body = neg ? s.slice(1) : s;
  const [ip, fp] = body.split(".");
  const den = Math.pow(10, fp.length);
  let num = parseInt(ip + fp, 10);
  if (neg) num = -num;
  const g = gcd(num, den);
  return { num: String(num / g), den: String(den / g) };
}

/** 秒输入转换，非法时返回 null（调用方用于表单校验）。 */
export function trySecsToRational(input: string | number): Rational | null {
  try {
    return secsToRational(input);
  } catch {
    return null;
  }
}

/** 有理数 -> 秒（数字，仅用于展示与可视化定位）。 */
export function rationalToSecs(rt: Rational | number | undefined | null): number {
  if (rt == null) return NaN;
  if (typeof rt === "number") return rt;
  if (typeof rt === "string") return Number(rt);
  if (typeof rt === "object" && rt.num != null && rt.den != null) {
    const n = Number(rt.num);
    const d = Number(rt.den);
    if (!d) return NaN;
    return n / d;
  }
  return NaN;
}

/** 有理数 -> num/den 展示串。 */
export function rationalText(rt: Rational | number | undefined | null): string {
  if (rt == null || typeof rt === "number" || typeof rt === "string") {
    const v = rationalToSecs(rt);
    return isNaN(v) ? "—" : fmtSecs(v);
  }
  return `${rt.num}/${rt.den}`;
}

/** 有理数 -> '≈ 3.500s' 展示。 */
export function rationalApprox(rt: Rational | number | undefined | null): string {
  const v = rationalToSecs(rt);
  if (isNaN(v)) return "—";
  return `≈ ${v.toFixed(3)}s`;
}

/** 秒格式化（避免 -0.000 等显示问题）。 */
export function fmtSecs(v: number): string {
  if (isNaN(v)) return "—";
  const r = Math.round(v * 1000) / 1000;
  return Number.isInteger(r) ? String(r) : String(r.toFixed(3));
}

/** 秒输入值转有理数后回写表单（展示预览文本用）。 */
export function secsPreview(input: string | number): string {
  const rt = trySecsToRational(input);
  if (!rt) return "无效";
  return `${rt.num}/${rt.den} · ${rationalApprox(rt)}`;
}