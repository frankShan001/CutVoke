import { stageCaption, releaseEdit, useInteractionProject } from "../store/interactive";
import { flushPendingEdits } from "../lib/pendingEdits";
import { FONT_FAMILIES } from "../lib/fontFamilies";
import { msg, tr, useLocale } from "../i18n";
/** 字幕面板：添加（文本 + 起止秒→rational）、列出当前字幕、删除、样式编辑、SRT 导入/导出。
    数据来源：project.sequence.captions（caption.add/update/remove）。
    SRT 走专用 HTTP 端点：GET /projects/{id}/captions.srt、POST /projects/{id}/captions/import。 */

import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { Captions, Plus, Trash2, Upload, Download, AlignLeft, AlignCenter, AlignRight, Bold } from "lucide-react";
import { Button, Field, Panel, Row, TextInput } from "./ui";
import { useEditor, showError } from "../store/editor";
import { addCaption, removeCaption, updateCaption } from "../store/clipEdit";
import { getLatestState, refreshProject, runCommand } from "../store/actions";
import { trySecsToRational, rationalText, rationalToSecs } from "../lib/rational";
import { ApiFailure, API_BASE } from "../lib/api";
import { FontStylePanel } from "./FontStylePanel";
import { CaptionAnimationSection } from "./CaptionAnimationSection";
import { CaptionGeometryEditor } from "./CaptionGeometryEditor";
import { SpeechRecognitionPanel } from "./SpeechRecognitionPanel";

type Align = "left" | "center" | "right";

interface StyleForm {
  fontSize: string;
  fontFamily: "Noto Sans SC" | "Noto Serif SC" | "Barlow Condensed" | "Space Grotesk" | "Cormorant Garamond" | "IBM Plex Mono";
  color: string;
  wordHighlightColor: string;
  strokeWidth: string;
  align: Align;
  bold: boolean;
}

interface CaptionDraft {
  text: string;
  start: string;
  end: string;
}

export function CaptionPanel() {
  useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const previewProject = useInteractionProject(state.project);
  const captions = previewProject?.sequence.captions || [];
  const composing = useRef(false);
  const readOnly = Boolean(state.editLock);
  const [text, setText] = useState("");
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("3");
  const selectedId = state.selectedCaptionId;
  const [captionSearch, setCaptionSearch] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);
  const [form, setForm] = useState<StyleForm>({
    fontSize: "",
    fontFamily: "Noto Sans SC",
    color: "#ffffff",
    wordHighlightColor: "#FFD54A",
    strokeWidth: "",
    align: "center",
    bold: false,
  });
  const [captionDraft, setCaptionDraft] = useState<CaptionDraft>({
    text: "",
    start: "0",
    end: "3",
  });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const selectedCaptionSnapshot = useRef<string | null>(null);
  const invalid = (message: string) => new ApiFailure({ status: 400, code: "INVALID_ARGUMENT", message });
  const selected = selectedId ? captions.find(c => c.id === selectedId) || null : null;
  useEffect(() => {
    if (!selected) { selectedCaptionSnapshot.current = null; return; }
    const snapshot = JSON.stringify(selected);
    if (snapshot === selectedCaptionSnapshot.current) return;
    selectedCaptionSnapshot.current = snapshot;
    setCaptionDraft({ text: selected.text, start: String(rationalToSecs(selected.start)), end: String(rationalToSecs(selected.end)) });
    setForm({ fontSize: selected.fontSize != null ? String(selected.fontSize) : "",
      fontFamily: FONT_FAMILIES.includes(selected.fontFamily as StyleForm["fontFamily"]) ? selected.fontFamily as StyleForm["fontFamily"] : "Noto Sans SC",
      color: selected.color || "#ffffff", wordHighlightColor: selected.wordHighlightColor || "#FFD54A",
      strokeWidth: selected.strokeWidth != null ? String(selected.strokeWidth) : "", align: selected.align || "center", bold: selected.bold || false });
  }, [selected]);
  const updateCaptionDraft = (next: CaptionDraft) => {
    setCaptionDraft(next);
    if (!selected || readOnly) return;
    const start = trySecsToRational(next.start), end = trySecsToRational(next.end);
    if (start && end && rationalToSecs(start) >= 0 && rationalToSecs(end) > rationalToSecs(start) && next.text.trim())
      stageCaption(dispatch, selected.id, { text: next.text, start, end }, composing.current);
  };
  const selectCaption = (captionId: string) => {
    const caption = captions.find(c => c.id === captionId);
    if (!caption) return;
    dispatch({ type: "CAPTION_SELECTION_SET", captionId });
    const time = getLatestState()?.playhead || 0;
    if (time < rationalToSecs(caption.start) || time >= rationalToSecs(caption.end))
      dispatch({ type: "PLAYHEAD_SET", t: rationalToSecs(caption.start) });
  };
  const beginNewCaption = () => dispatch({ type: "CAPTION_SELECTION_SET", captionId: null });

  const handleAdd = () => {
    if (!state.currentId) {
      showError(dispatch, invalid(tr("请先选择工程")));
      return;
    }
    if (!text.trim()) {
      showError(dispatch, invalid(tr("字幕文本不能为空")));
      return;
    }
    const s = trySecsToRational(start);
    const e = trySecsToRational(end);
    if (!s || !e) {
      showError(dispatch, invalid(tr("起止时间须为秒数字，如 1.5 / 3")));
      return;
    }
    if (rationalToSecs(e) <= rationalToSecs(s)) {
      showError(dispatch, invalid(tr("结束时间必须大于开始时间")));
      return;
    }
    void addCaption(dispatch, state, { text: text.trim(), start: s, end: e }).then((res) => {
      if (res.ok) {
        setText("");
        setStart("0");
        setEnd("3");
      }
    });
  };

  const handleRemove = (id: string) => {
    if (selectedId === id) {
    }
    void removeCaption(dispatch, state, id).then((res) => {
      if (res.ok && selectedId === id) {
        dispatch({ type: "CAPTION_SELECTION_SET", captionId: null });
      }
    });
  };

  /** 离散样式操作立即提交；数字输入由失焦/回车触发，避免每个字符各占一个 revision。 */
  const commitStyle = (patch: Partial<StyleForm>) => {
    const next: StyleForm = { ...form, ...patch };
    setForm(next);
    const caption = selectedId ? captions.find((item) => item.id === selectedId) : null;
    if (!caption) return;

    const update: Parameters<typeof updateCaption>[2] = { captionId: caption.id };
    if (patch.fontSize !== undefined) {
      const fontSize = next.fontSize === "" ? 0 : Math.round(Number(next.fontSize));
      if (fontSize !== (caption.fontSize ?? 0)) update.fontSize = fontSize;
    }
    if (patch.fontFamily !== undefined && next.fontFamily !== (caption.fontFamily || "Noto Sans SC")) {
      update.fontFamily = next.fontFamily;
    }
    if (patch.color !== undefined && next.color !== (caption.color || "#ffffff")) {
      update.color = next.color;
    }
    if (patch.wordHighlightColor !== undefined
        && next.wordHighlightColor !== (caption.wordHighlightColor || "")) {
      update.wordHighlightColor = next.wordHighlightColor;
    }
    if (patch.strokeWidth !== undefined) {
      const strokeWidth = next.strokeWidth === "" ? 0 : Math.round(Number(next.strokeWidth));
      if (strokeWidth !== (caption.strokeWidth ?? 0)) update.strokeWidth = strokeWidth;
    }
    if (patch.align !== undefined && next.align !== (caption.align || "center")) {
      update.align = next.align;
    }
    if (patch.bold !== undefined && next.bold !== Boolean(caption.bold)) {
      update.bold = next.bold;
    }
    if (Object.keys(update).length > 1 && Object.values(update).every(v => typeof v !== "number" || Number.isFinite(v)) && (update.fontSize === undefined || update.fontSize >= 1) && (update.strokeWidth === undefined || update.strokeWidth >= 0)) stageCaption(dispatch, caption.id, update);
  };

  const setStyleField = (patch: Partial<StyleForm>) => {
    commitStyle(patch);
  };

  const commitStyleOnEnter = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      e.currentTarget.blur();
    }
  };

  const commitCaptionDraft = () => { if (selected) releaseEdit(`caption:${selected.id}`); };

  // ---- SRT 导入/导出 ----
  const rejectWhileAgentEditing = () => {
    if (!state.editLock) return false;
    dispatch({
      type: "STATUS_SET",
      severity: "warn",
      text: msg("Agent {0} 正在编辑，页面暂时只读", state.editLock.owner),
    });
    return true;
  };

  const handleImportClick = () => {
    if (rejectWhileAgentEditing()) return;
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 允许重复选择同一文件
    if (!file) return;
    // 文件选择窗口打开期间 Agent 也可能刚取得租约；提交前再检查一次，
    // 避免这个非 command 入口给人一种“仍可改工程”的错觉。
    if (rejectWhileAgentEditing()) return;
    if (!state.currentId) {
      showError(dispatch, invalid(tr("请先选择工程")));
      return;
    }
    try {
      const srt = await file.text();
      const res = await fetch(
        `${API_BASE}/projects/${encodeURIComponent(state.currentId)}/captions/import`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ srt, actor: { kind: "human", id: "ui" } }),
        },
      );
      if (!res.ok) {
        const data = (await res.json().catch(() => undefined)) as
          | { error?: { message?: string } }
          | undefined;
        showError(
          dispatch,
          new ApiFailure({
            status: res.status,
            code: "IMPORT_FAILED",
            message: data?.error?.message || tr("SRT 导入失败"),
          }),
        );
        return;
      }
      const data = (await res.json()) as { imported?: number };
      await refreshProject(dispatch, state.currentId);
      dispatch({
        type: "STATUS_SET",
        severity: "ok",
        text: msg("已导入 {0} 条字幕", data.imported ?? 0),
      });
    } catch (err) {
      showError(dispatch, err);
    }
  };

  const handleExport = async () => {
    if (!await flushPendingEdits()) return;
    if (!state.currentId) {
      showError(dispatch, invalid(tr("请先选择工程")));
      return;
    }
    try {
      const res = await fetch(
        `${API_BASE}/projects/${encodeURIComponent(state.currentId)}/captions.srt`,
        { method: "GET" },
      );
      if (!res.ok) {
        showError(
          dispatch,
          new ApiFailure({ status: res.status, code: "EXPORT_FAILED", message: tr("SRT 导出失败") }),
        );
        return;
      }
      const srtText = await res.text();
      const blob = new Blob([srtText], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${state.projectName || state.currentId}.srt`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已导出 SRT") });
    } catch (err) {
      showError(dispatch, err);
    }
  };

  const startPreview = trySecsToRational(start);
  const endPreview = trySecsToRational(end);
  const visibleCaptions = captions.filter((caption) =>
    caption.text.toLowerCase().includes(captionSearch.trim().toLowerCase()));
  const batchTargets = visibleCaptions.filter((caption) => caption.id !== selectedId);
  const applyStyleToVisible = async () => {
    if (!selected || !batchTargets.length || readOnly || batchBusy) return;
    setBatchBusy(true);
    const style = {
      fontSize: selected.fontSize, fontFamily: selected.fontFamily, color: selected.color,
      strokeColor: selected.strokeColor, strokeWidth: selected.strokeWidth,
      background: selected.background, align: selected.align, bold: selected.bold,
      wordHighlightColor: selected.wordHighlightColor,
    };
    const result = await runCommand(dispatch, getLatestState() || state, "caption.patch", {
      updates: batchTargets.map((caption) => ({ captionId: caption.id, ...style })),
    });
    setBatchBusy(false);
    if (result.ok) {
      dispatch({ type: "STATUS_SET", severity: "ok",
        text: msg("已将当前字幕样式应用到 {0} 条字幕，可一次撤销", batchTargets.length) });
    } else if (result.error) showError(dispatch, result.error);
  };

  return (
    <Panel title={tr("字幕")} subtitle={tr("识别、导入与逐条编辑")}>
      <SpeechRecognitionPanel />
      <div className="cv-srt-row">
        <Row>
          <Button variant="secondary" size="sm" onClick={handleImportClick} aria-label={tr("导入SRT")} disabled={readOnly}>
            <Upload size={14} />{tr("导入 SRT")}</Button>
          <Button variant="secondary" size="sm" onClick={handleExport} aria-label={tr("导出SRT")}>
            <Download size={14} />{tr("导出 SRT")}</Button>
        </Row>
        <input
          ref={fileInputRef}
          type="file"
          accept=".srt"
          style={{ display: "none" }}
          onChange={handleFileChange}
        />
      </div>

      {selected ? (
        <Button
          variant="secondary"
          size="sm"
          full
          onClick={beginNewCaption}
          disabled={readOnly}
          style={{ marginTop: 8 }}
        >
          <Plus size={14} />{tr("新建字幕")}</Button>
      ) : (
        <>
          <Field label={tr("字幕文本")}>
            <TextInput value={text} onChange={(e) => setText(e.target.value)} placeholder={tr("如：开场字幕")} disabled={readOnly} />
          </Field>
          <div style={{ marginTop: 8 }}>
            <Row>
              <Field label={tr("开始 (s)")}>
                <TextInput type="number" step="any" value={start} onChange={(e) => setStart(e.target.value)} disabled={readOnly} />
              </Field>
              <Field label={tr("结束 (s)")}>
                <TextInput type="number" step="any" value={end} onChange={(e) => setEnd(e.target.value)} disabled={readOnly} />
              </Field>
            </Row>
          </div>
          <div style={{ marginTop: 4 }}>
            <span className="cv-hint" style={{ marginTop: 0 }}>
              {startPreview && endPreview
                ? `[${startPreview.num}/${startPreview.den} → ${endPreview.num}/${endPreview.den})`
                : tr("起止需为秒数字")}
            </span>
          </div>
          <Button variant="primary" full onClick={handleAdd} style={{ marginTop: 8 }} disabled={readOnly}>
            <Plus size={14} />{tr("添加字幕")}</Button>
        </>
      )}

      {captions.length > 8 ? (
        <input className="cv-input" aria-label={tr("搜索工程字幕")} placeholder={tr("搜索字幕文字")}
          value={captionSearch} onChange={(event) => setCaptionSearch(event.target.value)}
          style={{ width: "100%", marginTop: 10 }} />
      ) : null}
      <div className="cv-tracks" style={{ marginTop: 10 }}>
        {captions.length === 0 ? (
          <span className="cv-empty cv-empty--tight">{tr("暂无字幕")}</span>
        ) : visibleCaptions.length === 0 ? (
          <span className="cv-empty cv-empty--tight">{tr("没有匹配的字幕")}</span>
        ) : (
          visibleCaptions.map((c) => (
            <div
              key={c.id}
              className={`cv-caption-row${c.id === selectedId ? " cv-caption-row--active" : ""}`}
              onClick={() => selectCaption(c.id)}
              role="button"
              tabIndex={0}
              aria-label={tr("选择字幕 {0}", c.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  selectCaption(c.id);
                }
              }}
            >
              <Captions size={13} className="cv-ic--video" />
              <div className="cv-caption-row__body">
                <div className="cv-caption-row__text">{c.text}</div>
                <div className="cv-caption-row__time cv-mono">
                  [{rationalText(c.start)} → {rationalText(c.end)})
                </div>
              </div>
              <button
                className="cv-btn cv-btn--sm cv-btn--ghost"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRemove(c.id);
                }}
                disabled={readOnly}
                title={tr("删除字幕")}
                aria-label={tr("删除字幕 {0}", c.id)}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))
        )}
      </div>

      {selected ? (
        <div className="cv-style-edit">
          <div className="cv-style-edit__hint">{tr("编辑字幕：")}{selected.text.slice(0, 16) || tr("（空文本）")}
          </div>
          <Field label={tr("字幕文字")}>
            <textarea
              className="cv-input"
              rows={3}
              value={captionDraft.text}
              aria-label={tr("编辑字幕文字")}
              disabled={readOnly}
              onCompositionStart={() => { composing.current = true; }}
              onCompositionEnd={(e) => { composing.current = false; updateCaptionDraft({ ...captionDraft, text: e.currentTarget.value }); }}
              onChange={(e) => updateCaptionDraft({ ...captionDraft, text: e.target.value })}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                  e.preventDefault();
                  commitCaptionDraft();
                }
              }}
            />
          </Field>
          <Row>
            <Field label={tr("开始 (s)")}>
              <TextInput
                type="number"
                step="any"
                value={captionDraft.start}
                aria-label={tr("编辑字幕开始时间")}
                disabled={readOnly}
                onChange={(e) => updateCaptionDraft({ ...captionDraft, start: e.target.value })}
              />
            </Field>
            <Field label={tr("结束 (s)")}>
              <TextInput
                type="number"
                step="any"
                value={captionDraft.end}
                aria-label={tr("编辑字幕结束时间")}
                disabled={readOnly}
                onChange={(e) => updateCaptionDraft({ ...captionDraft, end: e.target.value })}
              />
            </Field>
          </Row>
          <CaptionGeometryEditor caption={selected} disabled={readOnly} />
          <Field label={tr("字体")}>
            <select className="cv-select" aria-label={tr("字幕字体")} value={form.fontFamily}
              disabled={readOnly}
              onChange={(event) => commitStyle({ fontFamily: event.target.value as StyleForm["fontFamily"] })}>
              <option value="Noto Sans SC">{tr("思源黑体 · Noto Sans SC")}</option>
              <option value="Noto Serif SC">{tr("思源宋体 · Noto Serif SC")}</option>
              <option value="Barlow Condensed">Barlow Condensed</option>
              <option value="Space Grotesk">Space Grotesk</option>
              <option value="Cormorant Garamond">Cormorant Garamond</option>
              <option value="IBM Plex Mono">IBM Plex Mono</option>

            </select>
          </Field>
          <FontStylePanel captionId={selectedId} disabled={readOnly} />
          <div className="cv-caption-word-highlight">
            <Button
              variant={selected.wordHighlightColor ? "primary" : "ghost"}
              size="sm"
              aria-label={tr("逐词高亮")}
              aria-pressed={Boolean(selected.wordHighlightColor)}
              disabled={readOnly || !selected.words?.length}
              onClick={() => commitStyle({ wordHighlightColor: selected.wordHighlightColor
                ? "" : (form.wordHighlightColor || "#FFD54A") })}
            >{tr("逐词高亮")}</Button>
            <input type="color" className="cv-input" aria-label={tr("逐词高亮颜色")}
              value={form.wordHighlightColor || "#FFD54A"}
              disabled={readOnly || !selected.words?.length || !selected.wordHighlightColor}
              onChange={(event) => setStyleField({ wordHighlightColor: event.target.value })}
              onBlur={() => commitStyle({ wordHighlightColor: form.wordHighlightColor })}
            />
            <span className="cv-hint">
              {selected.words?.length
                ? tr("{0} 个词级时间点；可与逐字入场同时使用。更改字幕文字后需重新识别时间。", selected.words.length)
                : tr("此字幕没有词级时间点。可从本地语音识别结果中创建带词时间的字幕。")}
            </span>
          </div>
          {batchTargets.length > 0 ? (
            <div className="cv-caption-batch">
              <span className="cv-hint">{tr("将这条字幕已保存的文字样式复制到")}{captionSearch.trim() ? tr("搜索结果中的其它 {0} 条", batchTargets.length) : tr("其它全部 {0} 条", batchTargets.length)}{tr("； 不修改文字、时码、位置或动画。")}</span>
              <Button variant="secondary" size="sm" full
                onClick={() => void applyStyleToVisible()} disabled={readOnly || batchBusy}>{tr("批量应用文字样式")}</Button>
            </div>
          ) : null}
          <CaptionAnimationSection caption={selected} disabled={readOnly} />
          <Row>
            <Field label={tr("字号")}>
              <TextInput
                type="number"
                step="1"
                min="1"
                value={form.fontSize}
                aria-label={tr("字幕字号")}
                disabled={readOnly}
                onChange={(e) => setStyleField({ fontSize: e.target.value })}
                onBlur={() => commitStyle({ fontSize: form.fontSize })}
                onKeyDown={commitStyleOnEnter}
              />
            </Field>
            <Field label={tr("描边宽度")}>
              <TextInput
                type="number"
                step="1"
                min="0"
                value={form.strokeWidth}
                aria-label={tr("描边宽度")}
                disabled={readOnly}
                onChange={(e) => setStyleField({ strokeWidth: e.target.value })}
                onBlur={() => commitStyle({ strokeWidth: form.strokeWidth })}
                onKeyDown={commitStyleOnEnter}
              />
            </Field>
          </Row>
          <Field label={tr("文字颜色")}>
            <input
              type="color"
              className="cv-input"
              value={form.color}
              aria-label={tr("字幕颜色")}
              disabled={readOnly}
              onChange={(e) => commitStyle({ color: e.target.value })}
            />
          </Field>
          <Field label={tr("对齐")}>
            <div role="group" aria-label={tr("字幕对齐")} className="cv-row cv-style-edit__align">
              <Button
                variant={form.align === "left" ? "primary" : "ghost"}
                size="sm"
                aria-label={tr("左对齐")}
                disabled={readOnly}
                onClick={() => commitStyle({ align: "left" })}
              >
                <AlignLeft size={14} />
              </Button>
              <Button
                variant={form.align === "center" ? "primary" : "ghost"}
                size="sm"
                aria-label={tr("居中")}
                disabled={readOnly}
                onClick={() => commitStyle({ align: "center" })}
              >
                <AlignCenter size={14} />
              </Button>
              <Button
                variant={form.align === "right" ? "primary" : "ghost"}
                size="sm"
                aria-label={tr("右对齐")}
                disabled={readOnly}
                onClick={() => commitStyle({ align: "right" })}
              >
                <AlignRight size={14} />
              </Button>
            </div>
          </Field>
          <Button
            variant={form.bold ? "primary" : "ghost"}
            size="sm"
            aria-label={tr("字幕加粗")}
            aria-pressed={form.bold}
            disabled={readOnly}
            onClick={() => commitStyle({ bold: !form.bold })}
          >
            <Bold size={14} />{tr("加粗")}</Button>
        </div>
      ) : null}
    </Panel>
  );
}
