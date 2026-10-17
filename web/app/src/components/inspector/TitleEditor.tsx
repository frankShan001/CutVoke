import { FONT_FAMILIES } from "../../lib/fontFamilies";
import { tr, useLocale } from "../../i18n";
/** 独立标题片段的内容、排版与动画参数，写回同一 cutvoke.text 效果。 */

import { useEffect, useState } from "react";
import type { Clip } from "../../types/api";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { updateEffect } from "../../store/effectEdit";

type Align = "left" | "center" | "right";
type InStyle = "none" | "fade" | "scale" | "typewriter" | "impact" | "slide-left" | "slide-up" | "flip" | "stagger" | "blur-in";
type OutStyle = "none" | "fade" | "scale" | "zoom-through" | "slide-right" | "scatter";
type LoopStyle = "none" | "pulse" | "blink" | "glitch" | "wave";
interface Form {
  content: string; fontSize: string; fontFamily: string; lineSpacing: string;
  color: string; strokeColor: string; strokeWidth: string;
  bold: boolean; background: string; panelWidth: string; panelHeight: string;
  align: Align; x: string; y: string;
  scale: string; rotation: string; shadow: string; animIn: string; animOut: string;
  animInStyle: InStyle; animOutStyle: OutStyle; animLoopStyle: LoopStyle; animLoopMs: string;
  animStrength: string; animStaggerMs: string; animGlow: string; animColor: string;
}
const defaults: Form = {
  content: "", fontSize: "48", fontFamily: "Noto Sans SC", lineSpacing: "1",
  color: "#ffffff", strokeColor: "#000000",
  strokeWidth: "2", bold: false, background: "", panelWidth: "0", panelHeight: "0", align: "center",
  x: "50", y: "50", scale: "1", rotation: "0", shadow: "1",
  animIn: "0", animOut: "0", animInStyle: "fade", animOutStyle: "fade",
  animLoopStyle: "none", animLoopMs: "1000",
  animStrength: "1", animStaggerMs: "45", animGlow: "0", animColor: "#ff39d4",
};

function fromParams(params: Record<string, unknown>): Form {
  const value = (key: keyof Form) => String(params[key] ?? defaults[key]);
  return {
    content: value("content"), fontSize: value("fontSize"),
    fontFamily: value("fontFamily"), lineSpacing: value("lineSpacing"),
    color: value("color"),
    strokeColor: value("strokeColor"), strokeWidth: value("strokeWidth"),
    bold: Boolean(params.bold ?? defaults.bold), background: value("background"),
    panelWidth: String(Math.round(Number(params.panelWidth ?? 0) * 100)),
    panelHeight: String(Math.round(Number(params.panelHeight ?? 0) * 100)),
    align: (params.align as Align) || "center",
    x: String(Math.round(Number(params.x ?? 0.5) * 100)),
    y: String(Math.round(Number(params.y ?? 0.5) * 100)),
    scale: value("scale"), rotation: value("rotation"), shadow: value("shadow"),
    animIn: value("animIn"), animOut: value("animOut"),
    animInStyle: (params.animInStyle as InStyle) || "fade",
    animOutStyle: (params.animOutStyle as OutStyle) || "fade",
    animLoopStyle: (params.animLoopStyle as LoopStyle) || "none",
    animLoopMs: value("animLoopMs"),
    animStrength: value("animStrength"), animStaggerMs: value("animStaggerMs"),
    animGlow: value("animGlow"), animColor: value("animColor"),
  };
}

export function TitleEditor({ clip }: { clip: Clip }) {
  useLocale();
  const { state, dispatch } = useEditor();
  const params = clip.effects?.find((effect) => effect.effectId === "cutvoke.text")?.params || {};
  const paramsKey = JSON.stringify(params);
  const [form, setForm] = useState<Form>(() => fromParams(params));
  const [saving, setSaving] = useState(false);
  useEffect(() => setForm(fromParams(params)), [clip.id, paramsKey]);

  const set = (key: keyof Form, value: string | boolean) => setForm((old) => ({ ...old, [key]: value }));
  const number = (key: keyof Form) => Number(form[key]);
  const anchorOutsideSafeArea = [number("x"), number("y")].some((value) =>
    Number.isFinite(value) && (value < 10 || value > 90));
  const valid = form.content.trim().length > 0 && form.content.length <= 180 &&
    number("fontSize") >= 8 && number("fontSize") <= 200 &&
    (FONT_FAMILIES as readonly string[]).includes(form.fontFamily) &&
    number("lineSpacing") >= 0.5 && number("lineSpacing") <= 2.5 &&
    number("panelWidth") >= 0 && number("panelWidth") <= 100 &&
    number("panelHeight") >= 0 && number("panelHeight") <= 100 &&
    number("strokeWidth") >= 0 && number("strokeWidth") <= 8 &&
    number("x") >= 0 && number("x") <= 100 && number("y") >= 0 && number("y") <= 100 &&
    number("scale") >= 0.1 && number("scale") <= 5 &&
    number("rotation") >= -180 && number("rotation") <= 180 &&
    Number.isInteger(number("shadow")) && number("shadow") >= 0 && number("shadow") <= 12 &&
    Number.isInteger(number("animIn")) && number("animIn") >= 0 && number("animIn") <= 3000 &&
    Number.isInteger(number("animOut")) && number("animOut") >= 0 && number("animOut") <= 3000 &&
    ["none", "fade", "scale", "typewriter", "impact", "slide-left", "slide-up", "flip", "stagger", "blur-in"].includes(form.animInStyle) &&
    ["none", "fade", "scale", "zoom-through", "slide-right", "scatter"].includes(form.animOutStyle) &&
    ["none", "pulse", "blink", "glitch", "wave"].includes(form.animLoopStyle) &&
    number("animStrength") >= 0.1 && number("animStrength") <= 3 &&
    number("animGlow") >= 0 && number("animGlow") <= 24 &&
    Number.isInteger(number("animStaggerMs")) && number("animStaggerMs") >= 0 && number("animStaggerMs") <= 250 &&
    /^#[0-9a-f]{6}$/i.test(form.animColor) &&
    Number.isInteger(number("animLoopMs")) && number("animLoopMs") >= 300 && number("animLoopMs") <= 3000;

  const numericFields: { key: keyof Form; label: string; min: number; max: number; step: number }[] = [
    { key: "fontSize", label: tr("字号"), min: 8, max: 200, step: 1 },
    { key: "lineSpacing", label: tr("多行行距"), min: 0.5, max: 2.5, step: 0.05 },
    { key: "strokeWidth", label: tr("描边宽度"), min: 0, max: 8, step: 0.5 },
    { key: "panelWidth", label: tr("背景条宽度 %"), min: 0, max: 100, step: 1 },
    { key: "panelHeight", label: tr("背景条高度 %"), min: 0, max: 100, step: 1 },
    { key: "x", label: tr("水平位置 %"), min: 0, max: 100, step: 1 },
    { key: "y", label: tr("垂直位置 %"), min: 0, max: 100, step: 1 },
    { key: "scale", label: tr("缩放"), min: 0.1, max: 5, step: 0.05 },
    { key: "rotation", label: tr("旋转角度"), min: -180, max: 180, step: 1 },
    { key: "shadow", label: tr("阴影宽度"), min: 0, max: 12, step: 1 },
    { key: "animIn", label: tr("淡入毫秒"), min: 0, max: 3000, step: 50 },
    { key: "animOut", label: tr("淡出毫秒"), min: 0, max: 3000, step: 50 },
    { key: "animLoopMs", label: tr("循环周期毫秒"), min: 300, max: 3000, step: 50 },
    { key: "animStrength", label: tr("动效强度"), min: 0.1, max: 3, step: 0.1 },
    { key: "animStaggerMs", label: tr("逐字间隔毫秒"), min: 0, max: 250, step: 5 },
    { key: "animGlow", label: tr("霓虹光晕"), min: 0, max: 24, step: 1 },
  ];
  const integerFields = new Set<keyof Form>(["shadow", "animIn", "animOut", "animLoopMs", "animStaggerMs"]);
  const invalidNumericField = numericFields.find((field) => {
    const value = number(field.key);
    return !Number.isFinite(value) || value < field.min || value > field.max ||
      (integerFields.has(field.key) && !Number.isInteger(value));
  });
  const validationMessage = !form.content.trim()
    ? tr("请先填写标题内容。")
    : form.content.length > 180
      ? tr("标题内容最多 180 个字符。")
      : invalidNumericField
        ? !Number.isFinite(number(invalidNumericField.key))
          ? tr("{0}请输入有效数字。", invalidNumericField.label)
          : integerFields.has(invalidNumericField.key) && !Number.isInteger(number(invalidNumericField.key))
            ? tr("{0}必须是整数。", invalidNumericField.label)
            : tr("{0}需在 {1}–{2} 范围内。", invalidNumericField.label, invalidNumericField.min, invalidNumericField.max)
        : !valid
          ? tr("标题样式参数无效，请检查输入后再应用。")
          : "";
  const validationMessageId = validationMessage ? `title-validation-${clip.id}` : undefined;

  const apply = async () => {
    if (!valid || saving) return;
    setSaving(true);
    try {
      await updateEffect(dispatch, getLatestState() || state, {
        clipId: clip.id, effectId: "cutvoke.text",
        params: {
          content: form.content.trim(), fontSize: number("fontSize"),
          fontFamily: form.fontFamily, lineSpacing: number("lineSpacing"), color: form.color,
          strokeColor: form.strokeColor, strokeWidth: number("strokeWidth"),
          bold: form.bold, background: form.background.trim(),
          panelWidth: number("panelWidth") / 100, panelHeight: number("panelHeight") / 100,
          align: form.align,
          x: number("x") / 100, y: number("y") / 100,
          scale: number("scale"), rotation: number("rotation"),
          shadow: number("shadow"), animIn: number("animIn"), animOut: number("animOut"),
          animInStyle: form.animInStyle, animOutStyle: form.animOutStyle,
          animLoopStyle: form.animLoopStyle, animLoopMs: number("animLoopMs"),
          animStrength: number("animStrength"), animStaggerMs: number("animStaggerMs"),
          animGlow: number("animGlow"), animColor: form.animColor,
        },
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="title-editor" aria-label={tr("文字标题属性")}>
      <strong>{tr("文字标题")}</strong>
      <div className="title-editor__actions" aria-label={tr("动效组合")}>
        <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary" onClick={() => setForm(old => ({ ...old,
          bold: true, animInStyle: "impact", animIn: "350", animOutStyle: "zoom-through", animOut: "300",
          animLoopStyle: "glitch", animLoopMs: "600", animGlow: "12", animStrength: "1.4", animColor: "#ff39d4" }))}>{tr("霓虹冲击")}</button>
        <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary" onClick={() => setForm(old => ({ ...old,
          bold: true, animInStyle: "stagger", animIn: "500", animOutStyle: "scatter", animOut: "350",
          animLoopStyle: "wave", animLoopMs: "800", animGlow: "8", animStrength: "1", animColor: "#32f5ff" }))}>{tr("逐字浪潮")}</button>
        <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary" onClick={() => setForm(old => ({ ...old,
          bold: true, animInStyle: "flip", animIn: "400", animOutStyle: "slide-right", animOut: "300",
          animLoopStyle: "none", animGlow: "6", animStrength: "1", animColor: "#b8ff39" }))}>{tr("翻转飞出")}</button>
      </div>
      <label>{tr("内容")}<textarea className="cv-input" rows={3} maxLength={180} aria-label={tr("编辑标题内容")}
          value={form.content} onChange={(event) => set("content", event.target.value)} />
      </label>
      <label>{tr("字体")}<select className="cv-input" aria-label={tr("标题字体")} value={form.fontFamily}
          onChange={(event) => set("fontFamily", event.target.value)}>
          <option value="Noto Sans SC">{tr("思源黑体风格 · Noto Sans SC")}</option>
          <option value="Noto Serif SC">{tr("思源宋体风格 · Noto Serif SC")}</option>
          <option value="Barlow Condensed">Barlow Condensed</option>
          <option value="Space Grotesk">Space Grotesk</option>
          <option value="Cormorant Garamond">Cormorant Garamond</option>
          <option value="IBM Plex Mono">IBM Plex Mono</option>

        </select>
      </label>
      <div className="title-editor__two">
        <label>{tr("文字颜色")}<input className="cv-input" type="color" aria-label={tr("标题文字颜色")}
          value={form.color} onChange={(event) => set("color", event.target.value)} /></label>
        <label>{tr("描边颜色")}<input className="cv-input" type="color" aria-label={tr("标题描边颜色")}
          value={form.strokeColor} onChange={(event) => set("strokeColor", event.target.value)} /></label>
      </div>
      <label>{tr("背景色（留空为透明）")}<input className="cv-input" type="text" aria-label={tr("标题背景色")} placeholder="#1e1e2e"
          value={form.background} onChange={(event) => set("background", event.target.value)} />
      </label>
      <p className="cv-hint">{tr("背景条宽度和高度均大于 0 时绘制独立色块；任一为 0 时背景只包住文字。")}</p>
      <div className="title-editor__two">
        <label>{tr("对齐")}<select className="cv-input" aria-label={tr("标题对齐")} value={form.align}
          onChange={(event) => set("align", event.target.value)}>
          <option value="left">{tr("左")}</option><option value="center">{tr("中")}</option><option value="right">{tr("右")}</option>
        </select></label>
        <label className="title-editor__check"><input type="checkbox" checked={form.bold}
          onChange={(event) => set("bold", event.target.checked)} />{tr("加粗")}</label>
      </div>
      <div className="title-editor__two">
        {numericFields.map((field) => (
          <label key={field.key}>{field.label}
            <input className="cv-input" type="number" aria-label={tr("标题{0}", field.label)}
              min={field.min} max={field.max} step={field.step} value={String(form[field.key])}
              onChange={(event) => set(field.key, event.target.value)} />
          </label>
        ))}
      </div>
      <div className="title-editor__two">
        <label>{tr("入场方式")}<select className="cv-input" aria-label={tr("标题入场方式")} value={form.animInStyle}
            onChange={(event) => { const style = event.target.value as InStyle;
              setForm(old => ({ ...old, animInStyle: style,
                animIn: style !== "none" && Number(old.animIn) === 0 ? "300" : old.animIn })); }}>
            <option value="none">{tr("无")}</option><option value="fade">{tr("淡入")}</option>
            <option value="scale">{tr("缩放出现")}</option><option value="typewriter">{tr("逐字出现")}</option>
            <option value="impact">{tr("冲击弹入")}</option><option value="slide-left">{tr("左侧弹入")}</option>
            <option value="slide-up">{tr("向上弹入")}</option><option value="flip">{tr("立体翻入")}</option>
            <option value="stagger">{tr("逐字弹跳")}</option><option value="blur-in">{tr("聚焦显现")}</option>
          </select>
        </label>
        <label>{tr("出场方式")}<select className="cv-input" aria-label={tr("标题出场方式")} value={form.animOutStyle}
            onChange={(event) => { const style = event.target.value as OutStyle;
              setForm(old => ({ ...old, animOutStyle: style,
                animOut: style !== "none" && Number(old.animOut) === 0 ? "250" : old.animOut })); }}>
            <option value="none">{tr("无")}</option><option value="fade">{tr("淡出")}</option>
            <option value="scale">{tr("缩小消失")}</option>
            <option value="zoom-through">{tr("放大穿出")}</option><option value="slide-right">{tr("右侧飞出")}</option>
            <option value="scatter">{tr("逐字散开")}</option>
          </select>
        </label>
        <label>{tr("循环方式")}<select className="cv-input" aria-label={tr("标题循环方式")} value={form.animLoopStyle}
            onChange={(event) => set("animLoopStyle", event.target.value)}>
            <option value="none">{tr("无")}</option><option value="pulse">{tr("呼吸放大")}</option>
            <option value="blink">{tr("闪烁")}</option>
            <option value="glitch">{tr("故障色分离")}</option><option value="wave">{tr("逐字波浪")}</option>
          </select>
        </label>
      </div>
      <label>{tr("光晕颜色")}<input className="cv-input" type="color" aria-label={tr("标题光晕颜色")}
          value={form.animColor} onChange={(event) => set("animColor", event.target.value)} /></label>
      {validationMessage ? <p id={validationMessageId} className="cv-hint" role="status">
        {validationMessage}
      </p> : null}
      <div className="title-editor__actions">
        <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
          onClick={() => setForm({ ...defaults, content: form.content })}>{tr("复位样式")}</button>
        <button type="button" className="cv-btn cv-btn--sm" disabled={!valid || saving}
          aria-describedby={validationMessageId}
          onClick={() => void apply()}>{saving ? tr("保存中…") : tr("应用标题属性")}</button>
      </div>
      {anchorOutsideSafeArea ? (
        <p className="cv-hint" role="status">{tr("标题锚点超出四边 10% 参考安全区；预览栏可打开“安全区”检查完整文字。")}</p>
      ) : null}
      <p className="cv-hint">{tr("数值写入工程后可撤销；画面以连续预览和导出结果为准。")}</p>
    </section>
  );
}
