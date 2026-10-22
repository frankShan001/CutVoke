/** Seek-safe title geometry in project pixels, matching title_motion.py. */
import type { Clip } from "../types/api";
import { rationalToSecs } from "./rational";
import { evaluateKeyframes } from "./keyframes";
import fontMetrics from "./titleFontMetrics.json";

export type TitleParams = Record<string, unknown>;
export type TitleGlyph = { text: string; x: number; y: number; scale: number;
  rotation: number; opacity: number; flip: number; blur: number; width: number };
const clamp = (n: number) => Math.max(0, Math.min(1, n));
const back = (p: number) => 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2;
const measure = document.createElement("canvas").getContext("2d")!;

export function titleLayout(clip: Clip, p: TitleParams, time: number, width: number, height: number) {
  const n = (key: string, fallback: number) => Number(p[key] ?? fallback);
  const start = rationalToSecs(clip.timelineStart), duration = rationalToSecs(clip.timelineEnd) - start;
  const fpsTime = Math.max(0, time - start);
  const enter = p.animInStyle === "none" ? 0 : Math.min(n("animIn", 0) / 1000, duration);
  const leave = p.animOutStyle === "none" ? 0 : Math.min(n("animOut", 0) / 1000, Math.max(0, duration - enter));
  const strength = n("animStrength", 1), cycle = Math.max(.3, n("animLoopMs", 1000) / 1000);
  const advanced = Object.values(clip.keyframes || {}).some(lane => lane.length) ||
    ["impact", "slide-left", "slide-up", "flip", "stagger", "blur-in"].includes(String(p.animInStyle)) ||
    ["zoom-through", "slide-right", "scatter"].includes(String(p.animOutStyle)) ||
    ["glitch", "wave", "float"].includes(String(p.animLoopStyle)) ||
    ["animGlow", "outerStrokeWidth", "blockShadowX", "blockShadowY"].some(key => n(key, 0) !== 0);
  const family = String(p.fontFamily || "Noto Sans SC");
  const metrics = fontMetrics[family as keyof typeof fontMetrics] || fontMetrics["Noto Sans SC"];
  const em = Math.max(8, Math.round(n("fontSize", 48) * height / 1080));
  // Legacy ASS titles use real-height font sizing; motion titles use CSS em.
  const fontSize = advanced ? em : em / metrics.height;
  measure.font = `${p.bold ? 700 : 400} ${fontSize}px "${family}"`;
  const lane = (key: string, fallback: number) => evaluateKeyframes(clip.keyframes?.[key], fpsTime, fallback);
  const x = lane("x", n("x", .5) * width), y = lane("y", n("y", .5) * height);
  const scale = lane("scale", n("scale", 1)), rotation = lane("rotation", n("rotation", 0));
  const opacity = lane("opacity", 1), split = p.animInStyle === "stagger" || p.animOutStyle === "scatter" || p.animLoopStyle === "wave";
  const rows = String(p.content ?? "").replace(/\r\n/g, "\n").split("\n");
  const lineStep = em * n("lineSpacing", 1) * 1.25 * scale;
  const glyphs: TitleGlyph[] = [];
  const align = p.align || "center";
  for (const [rowIndex, row] of rows.entries()) {
    const rowY = y + (advanced ? rowIndex - (rows.length - 1) / 2 : rowIndex - rows.length + 1) * lineStep;
    const units = split ? Array.from(new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(row), value => value.segment) : [row];
    const lengths = units.map(unit => measure.measureText(unit).width);
    const total = lengths.reduce((a, b) => a + b, 0) * scale;
    let cursor = x - (align === "center" ? total / 2 : align === "right" ? total : 0);
    for (const [index, text] of units.entries()) {
      glyphs.push({ text, x: split ? cursor + lengths[index] * scale / 2 : x, y: rowY,
        scale, rotation, opacity, flip: 0, blur: 0, width: lengths[index] });
      cursor += lengths[index] * scale;
    }
  }
  const stagger = Math.min(n("animStaggerMs", 45) / 1000, enter * .65 / Math.max(1, glyphs.length - 1));
  const glitch = p.animLoopStyle === "glitch" && fpsTime % cycle < Math.min(.16, cycle * .22);
  for (const [index, glyph] of glyphs.entries()) {
    const g = glyph;
    const a = enter > 0 ? clamp(fpsTime / enter) : 1;
    if (p.animInStyle === "impact") {
      g.scale *= a <= .65 ? 1 + 1.8 * strength + (.86 - (1 + 1.8 * strength)) * (1 - (1 - a / .65) ** 3) : .86 + .14 * (1 - (1 - (a - .65) / .35) ** 2);
      g.rotation -= 10 * strength * (1 - a) ** 2; g.opacity *= clamp(a * 8);
    } else if (p.animInStyle === "slide-left" || p.animInStyle === "slide-up") {
      const offset = (1 - back(a)) * strength;
      if (p.animInStyle === "slide-left") g.x -= width * .45 * offset; else g.y += height * .4 * offset;
      g.opacity *= clamp(a * 6);
    } else if (p.animInStyle === "flip") {
      g.flip = 85 * (1 - a) ** 2; g.scale *= .65 + .35 * back(a); g.opacity *= clamp(a * 5);
    } else if (p.animInStyle === "stagger") {
      const q = clamp((fpsTime - index * stagger) / Math.max(.001, enter - stagger * (glyphs.length - 1)));
      g.y += height * .3 * strength * (1 - q) ** 2; g.scale *= Math.max(.05, back(q));
      g.rotation += (index % 2 ? -1 : 1) * 24 * strength * (1 - q) ** 2; g.opacity *= clamp(q * 5);
    } else if (p.animInStyle === "blur-in") {
      g.blur = 18 * strength * (1 - a) ** 2; g.scale *= 1 + .35 * (1 - a); g.opacity *= a;
    } else if (p.animInStyle === "scale") { g.scale *= .6 + .4 * a; g.opacity *= a;
    } else if (p.animInStyle === "fade" || !p.animInStyle) g.opacity *= a;
    if (p.animInStyle === "typewriter" && enter > 0) {
      const units = Array.from(new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(g.text), value => value.segment);
      const steps = Math.min(units.length, Math.max(2, Math.floor(enter * 1000 / 33)));
      const progress = advanced ? a : Math.min(steps, Math.floor(fpsTime * steps / enter) + 1) / Math.max(1, steps);
      g.text = units.slice(0, Math.max(1, Math.ceil(units.length * progress))).join("");
    }
    if (p.animLoopStyle === "wave") {
      const phase = fpsTime / cycle * Math.PI * 2 + index * .65;
      g.y += em * scale * .14 * strength * Math.sin(phase); g.rotation += 7 * strength * Math.sin(phase);
    } else if (p.animLoopStyle === "float") {
      g.y += em * scale * .08 * strength * Math.sin(fpsTime / cycle * Math.PI * 2);
      g.rotation += 1.5 * strength * Math.sin(fpsTime / cycle * Math.PI * 2);
    } else if (p.animLoopStyle === "pulse" || p.animLoopStyle === "blink") {
      if (advanced) {
        if (p.animLoopStyle === "pulse") g.scale *= 1 + .22 * Math.sin(Math.max(0, fpsTime - enter) / cycle * Math.PI) ** 2;
        else g.opacity *= .27 + .73 * Math.cos(fpsTime / cycle * Math.PI) ** 2;
      } else {
        const completedCycles = Math.floor((duration - leave - enter) / cycle + 1e-7);
        const loopTime = Math.max(0, fpsTime - enter);
        const phase = loopTime < completedCycles * cycle ? (loopTime % cycle) / cycle : 0;
        const triangle = 1 - Math.abs(2 * phase - 1);
        if (p.animLoopStyle === "pulse") g.scale *= 1 + .22 * triangle;
        else g.opacity *= 1 - 187 / 255 * triangle;
      }
    }
    const q = leave > 0 ? clamp((fpsTime - (duration - leave)) / leave) : 0;
    if (p.animOutStyle === "zoom-through") { g.scale *= 1 + 5 * strength * q ** 3; g.opacity *= 1 - clamp((q - .5) * 2);
    } else if (p.animOutStyle === "slide-right") { g.x += width * .65 * strength * q ** 2; g.opacity *= 1 - q;
    } else if (p.animOutStyle === "scatter") {
      const direction = index % 2 ? 1 : -1;
      g.x += direction * width * .35 * strength * q ** 2; g.y += height * (.15 + index % 3 * .12) * q ** 2;
      g.rotation += direction * 95 * q; g.opacity *= 1 - q;
    } else if (p.animOutStyle === "scale") { g.scale *= 1 - .4 * q; g.opacity *= 1 - q;
    } else if (p.animOutStyle === "fade" || !p.animOutStyle) g.opacity *= 1 - q;
    if (glitch) { g.x += Math.sin(fpsTime * 157) * 12 * strength * height / 1080; g.rotation += Math.sin(fpsTime * 93) * strength; }
  }
  const anchor = split ? "middle" : align === "left" ? "start" : align === "right" ? "end" : "middle";
  const baseline = (advanced ? (metrics.ascent - metrics.descent) / 2 : -metrics.descent) * fontSize;
  return { glyphs, fontSize, family, advanced, anchor, baseline, metrics, glitch, x, y, em };
}
