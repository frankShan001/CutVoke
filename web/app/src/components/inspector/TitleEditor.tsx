/** 独立标题片段的内容、排版与动画参数，写回同一 cutvoke.text 效果。 */

import { useEffect, useState } from "react";
import type { Clip } from "../../types/api";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { updateEffect } from "../../store/effectEdit";

type Align = "left" | "center" | "right";
type InStyle = "none" | "fade" | "scale" | "typewriter";
type OutStyle = "none" | "fade" | "scale";
type LoopStyle = "none" | "pulse" | "blink";
interface Form {
  content: string; fontSize: string; fontFamily: string; lineSpacing: string;
  color: string; strokeColor: string; strokeWidth: string;
  bold: boolean; background: string; panelWidth: string; panelHeight: string;
  align: Align; x: string; y: string;
  scale: string; rotation: string; shadow: string; animIn: string; animOut: string;
  animInStyle: InStyle; animOutStyle: OutStyle; animLoopStyle: LoopStyle; animLoopMs: string;
}
const defaults: Form = {
  content: "", fontSize: "48", fontFamily: "Noto Sans SC", lineSpacing: "1",
  color: "#ffffff", strokeColor: "#000000",
  strokeWidth: "2", bold: false, background: "", panelWidth: "0", panelHeight: "0", align: "center",
  x: "50", y: "50", scale: "1", rotation: "0", shadow: "1",
  animIn: "0", animOut: "0", animInStyle: "fade", animOutStyle: "fade",
  animLoopStyle: "none", animLoopMs: "1000",
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
  };
}

export function TitleEditor({ clip }: { clip: Clip }) {
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
    ["Noto Sans SC", "Noto Serif SC"].includes(form.fontFamily) &&
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
    ["none", "fade", "scale", "typewriter"].includes(form.animInStyle) &&
    ["none", "fade", "scale"].includes(form.animOutStyle) &&
    ["none", "pulse", "blink"].includes(form.animLoopStyle) &&
    Number.isInteger(number("animLoopMs")) && number("animLoopMs") >= 300 && number("animLoopMs") <= 3000;

  const numericFields: { key: keyof Form; label: string; min: number; max: number; step: number }[] = [
    { key: "fontSize", label: "字号", min: 8, max: 200, step: 1 },
    { key: "lineSpacing", label: "多行行距", min: 0.5, max: 2.5, step: 0.05 },
    { key: "strokeWidth", label: "描边宽度", min: 0, max: 8, step: 0.5 },
    { key: "panelWidth", label: "背景条宽度 %", min: 0, max: 100, step: 1 },
    { key: "panelHeight", label: "背景条高度 %", min: 0, max: 100, step: 1 },
    { key: "x", label: "水平位置 %", min: 0, max: 100, step: 1 },
    { key: "y", label: "垂直位置 %", min: 0, max: 100, step: 1 },
    { key: "scale", label: "缩放", min: 0.1, max: 5, step: 0.05 },
    { key: "rotation", label: "旋转角度", min: -180, max: 180, step: 1 },
    { key: "shadow", label: "阴影宽度", min: 0, max: 12, step: 1 },
    { key: "animIn", label: "淡入毫秒", min: 0, max: 3000, step: 50 },
    { key: "animOut", label: "淡出毫秒", min: 0, max: 3000, step: 50 },
    { key: "animLoopMs", label: "循环周期毫秒", min: 300, max: 3000, step: 50 },
  ];
  const integerFields = new Set<keyof Form>(["shadow", "animIn", "animOut", "animLoopMs"]);
  const invalidNumericField = numericFields.find((field) => {
    const value = number(field.key);
    return !Number.isFinite(value) || value < field.min || value > field.max ||
      (integerFields.has(field.key) && !Number.isInteger(value));
  });
  const validationMessage = !form.content.trim()
    ? "请先填写标题内容。"
    : form.content.length > 180
      ? "标题内容最多 180 个字符。"
      : invalidNumericField
        ? !Number.isFinite(number(invalidNumericField.key))
          ? `${invalidNumericField.label}请输入有效数字。`
          : integerFields.has(invalidNumericField.key) && !Number.isInteger(number(invalidNumericField.key))
            ? `${invalidNumericField.label}必须是整数。`
            : `${invalidNumericField.label}需在 ${invalidNumericField.min}–${invalidNumericField.max} 范围内。`
        : !valid
          ? "标题样式参数无效，请检查输入后再应用。"
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
        },
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="title-editor" aria-label="文字标题属性">
      <strong>文字标题</strong>
      <label>内容
        <textarea className="cv-input" rows={3} maxLength={180} aria-label="编辑标题内容"
          value={form.content} onChange={(event) => set("content", event.target.value)} />
      </label>
      <label>字体
        <select className="cv-input" aria-label="标题字体" value={form.fontFamily}
          onChange={(event) => set("fontFamily", event.target.value)}>
          <option value="Noto Sans SC">思源黑体风格 · Noto Sans SC</option>
          <option value="Noto Serif SC">思源宋体风格 · Noto Serif SC</option>
        </select>
      </label>
      <div className="title-editor__two">
        <label>文字颜色<input className="cv-input" type="color" aria-label="标题文字颜色"
          value={form.color} onChange={(event) => set("color", event.target.value)} /></label>
        <label>描边颜色<input className="cv-input" type="color" aria-label="标题描边颜色"
          value={form.strokeColor} onChange={(event) => set("strokeColor", event.target.value)} /></label>
      </div>
      <label>背景色（留空为透明）
        <input className="cv-input" type="text" aria-label="标题背景色" placeholder="#1e1e2e"
          value={form.background} onChange={(event) => set("background", event.target.value)} />
      </label>
      <p className="cv-hint">背景条宽度和高度均大于 0 时绘制独立色块；任一为 0 时背景只包住文字。</p>
      <div className="title-editor__two">
        <label>对齐<select className="cv-input" aria-label="标题对齐" value={form.align}
          onChange={(event) => set("align", event.target.value)}>
          <option value="left">左</option><option value="center">中</option><option value="right">右</option>
        </select></label>
        <label className="title-editor__check"><input type="checkbox" checked={form.bold}
          onChange={(event) => set("bold", event.target.checked)} />加粗</label>
      </div>
      <div className="title-editor__two">
        {numericFields.map((field) => (
          <label key={field.key}>{field.label}
            <input className="cv-input" type="number" aria-label={`标题${field.label}`}
              min={field.min} max={field.max} step={field.step} value={String(form[field.key])}
              onChange={(event) => set(field.key, event.target.value)} />
          </label>
        ))}
      </div>
      <div className="title-editor__two">
        <label>入场方式
          <select className="cv-input" aria-label="标题入场方式" value={form.animInStyle}
            onChange={(event) => set("animInStyle", event.target.value)}>
            <option value="none">无</option><option value="fade">淡入</option>
            <option value="scale">缩放出现</option><option value="typewriter">逐字出现</option>
          </select>
        </label>
        <label>出场方式
          <select className="cv-input" aria-label="标题出场方式" value={form.animOutStyle}
            onChange={(event) => set("animOutStyle", event.target.value)}>
            <option value="none">无</option><option value="fade">淡出</option>
            <option value="scale">缩小消失</option>
          </select>
        </label>
        <label>循环方式
          <select className="cv-input" aria-label="标题循环方式" value={form.animLoopStyle}
            onChange={(event) => set("animLoopStyle", event.target.value)}>
            <option value="none">无</option><option value="pulse">呼吸放大</option>
            <option value="blink">闪烁</option>
          </select>
        </label>
      </div>
      {validationMessage ? <p id={validationMessageId} className="cv-hint" role="status">
        {validationMessage}
      </p> : null}
      <div className="title-editor__actions">
        <button type="button" className="cv-btn cv-btn--sm cv-btn--secondary"
          onClick={() => setForm({ ...defaults, content: form.content })}>复位样式</button>
        <button type="button" className="cv-btn cv-btn--sm" disabled={!valid || saving}
          aria-describedby={validationMessageId}
          onClick={() => void apply()}>{saving ? "保存中…" : "应用标题属性"}</button>
      </div>
      {anchorOutsideSafeArea ? (
        <p className="cv-hint" role="status">标题锚点超出四边 10% 参考安全区；预览栏可打开“安全区”检查完整文字。</p>
      ) : null}
      <p className="cv-hint">数值写入工程后可撤销；画面以连续预览和导出结果为准。</p>
    </section>
  );
}
