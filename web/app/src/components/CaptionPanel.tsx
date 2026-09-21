/** 字幕面板：添加（文本 + 起止秒→rational）、列出当前字幕、删除、样式编辑、SRT 导入/导出。
    数据来源：project.sequence.captions（caption.add/update/remove）。
    SRT 走专用 HTTP 端点：GET /projects/{id}/captions.srt、POST /projects/{id}/captions/import。 */

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Captions, Plus, Trash2, Upload, Download, AlignLeft, AlignCenter, AlignRight, Bold } from "lucide-react";
import { Button, Field, Panel, Row, TextInput } from "./ui";
import { useEditor, showError } from "../store/editor";
import { addCaption, removeCaption, updateCaption } from "../store/clipEdit";
import { refreshProject } from "../store/actions";
import { trySecsToRational, rationalText, rationalToSecs } from "../lib/rational";
import { ApiFailure, API_BASE } from "../lib/api";
import { FontStylePanel } from "./FontStylePanel";
import { CaptionAnimationSection } from "./CaptionAnimationSection";
import { CaptionGeometryEditor } from "./CaptionGeometryEditor";

type Align = "left" | "center" | "right";

interface StyleForm {
  fontSize: string;
  color: string;
  strokeWidth: string;
  align: Align;
  bold: boolean;
}

export function CaptionPanel() {
  const { state, dispatch } = useEditor();
  const captions = state.project?.sequence.captions || [];
  const [text, setText] = useState("");
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("3");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<StyleForm>({
    fontSize: "",
    color: "#ffffff",
    strokeWidth: "",
    align: "center",
    bold: false,
  });
  const fileInputRef = useRef<HTMLInputElement>(null);

  const invalid = (message: string) =>
    new ApiFailure({ status: 400, code: "INVALID_ARGUMENT", message });

  // 选中字幕切换时，把该字幕的样式同步到编辑表单（仅在选择变化时同步，避免打断输入）。
  useEffect(() => {
    if (!selectedId) return;
    const c = captions.find((x) => x.id === selectedId);
    if (!c) return;
    setForm({
      fontSize: c.fontSize != null ? String(c.fontSize) : "",
      color: c.color || "#ffffff",
      strokeWidth: c.strokeWidth != null ? String(c.strokeWidth) : "",
      align: c.align || "center",
      bold: c.bold || false,
    });
    // 仅依赖 selectedId；captions 变化时不同步，表单以本地态为准。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const selected = selectedId ? captions.find((c) => c.id === selectedId) || null : null;

  const handleAdd = () => {
    if (!state.currentId) {
      showError(dispatch, invalid("请先选择工程"));
      return;
    }
    if (!text.trim()) {
      showError(dispatch, invalid("字幕文本不能为空"));
      return;
    }
    const s = trySecsToRational(start);
    const e = trySecsToRational(end);
    if (!s || !e) {
      showError(dispatch, invalid("起止时间须为秒数字，如 1.5 / 3"));
      return;
    }
    if (rationalToSecs(e) <= rationalToSecs(s)) {
      showError(dispatch, invalid("结束时间必须大于开始时间"));
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
    void removeCaption(dispatch, state, id).then((res) => {
      if (res.ok && selectedId === id) setSelectedId(null);
    });
  };

  /** 合并补丁：更新本地表单并发送 caption.update（仅变更字段）。 */
  const applyStyle = (patch: Partial<StyleForm>) => {
    const next: StyleForm = { ...form, ...patch };
    setForm(next);
    if (!selectedId) return;
    void updateCaption(dispatch, state, {
      captionId: selectedId,
      ...(patch.fontSize !== undefined
        ? { fontSize: next.fontSize === "" ? 0 : Math.round(Number(next.fontSize)) }
        : {}),
      ...(patch.color !== undefined ? { color: next.color } : {}),
      ...(patch.strokeWidth !== undefined
        ? { strokeWidth: next.strokeWidth === "" ? 0 : Math.round(Number(next.strokeWidth)) }
        : {}),
      ...(patch.align !== undefined ? { align: next.align } : {}),
      ...(patch.bold !== undefined ? { bold: next.bold } : {}),
    });
  };

  // ---- SRT 导入/导出 ----
  const handleImportClick = () => fileInputRef.current?.click();

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 允许重复选择同一文件
    if (!file) return;
    if (!state.currentId) {
      showError(dispatch, invalid("请先选择工程"));
      return;
    }
    try {
      const srt = await file.text();
      const res = await fetch(
        `${API_BASE}/projects/${encodeURIComponent(state.currentId)}/captions/import`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ srt }),
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
            message: data?.error?.message || "SRT 导入失败",
          }),
        );
        return;
      }
      const data = (await res.json()) as { imported?: number };
      await refreshProject(dispatch, state.currentId);
      dispatch({
        type: "STATUS_SET",
        severity: "ok",
        text: `已导入 ${data.imported ?? 0} 条字幕`,
      });
    } catch (err) {
      showError(dispatch, err);
    }
  };

  const handleExport = async () => {
    if (!state.currentId) {
      showError(dispatch, invalid("请先选择工程"));
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
          new ApiFailure({ status: res.status, code: "EXPORT_FAILED", message: "SRT 导出失败" }),
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
      dispatch({ type: "STATUS_SET", severity: "ok", text: "已导出 SRT" });
    } catch (err) {
      showError(dispatch, err);
    }
  };

  const startPreview = trySecsToRational(start);
  const endPreview = trySecsToRational(end);

  return (
    <Panel title="字幕" subtitle="纯文本字幕">
      <div className="cv-srt-row">
        <Row>
          <Button variant="secondary" size="sm" onClick={handleImportClick} aria-label="导入SRT">
            <Upload size={14} />
            导入 SRT
          </Button>
          <Button variant="secondary" size="sm" onClick={handleExport} aria-label="导出SRT">
            <Download size={14} />
            导出 SRT
          </Button>
        </Row>
        <input
          ref={fileInputRef}
          type="file"
          accept=".srt"
          style={{ display: "none" }}
          onChange={handleFileChange}
        />
      </div>

      <Field label="字幕文本">
        <TextInput value={text} onChange={(e) => setText(e.target.value)} placeholder="如：开场字幕" />
      </Field>
      <div style={{ marginTop: 8 }}>
        <Row>
          <Field label="开始 (s)">
            <TextInput type="number" step="any" value={start} onChange={(e) => setStart(e.target.value)} />
          </Field>
          <Field label="结束 (s)">
            <TextInput type="number" step="any" value={end} onChange={(e) => setEnd(e.target.value)} />
          </Field>
        </Row>
      </div>
      <div style={{ marginTop: 4 }}>
        <span className="cv-hint" style={{ marginTop: 0 }}>
          {startPreview && endPreview
            ? `[${startPreview.num}/${startPreview.den} → ${endPreview.num}/${endPreview.den})`
            : "起止需为秒数字"}
        </span>
      </div>
      <Button variant="primary" full onClick={handleAdd} style={{ marginTop: 8 }}>
        <Plus size={14} />
        添加字幕
      </Button>

      <div className="cv-tracks" style={{ marginTop: 10 }}>
        {captions.length === 0 ? (
          <span className="cv-empty cv-empty--tight">暂无字幕</span>
        ) : (
          captions.map((c) => (
            <div
              key={c.id}
              className={`cv-caption-row${c.id === selectedId ? " cv-caption-row--active" : ""}`}
              onClick={() => setSelectedId(c.id)}
              role="button"
              tabIndex={0}
              aria-label={`选择字幕 ${c.id}`}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setSelectedId(c.id);
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
                title="删除字幕"
                aria-label={`删除字幕 ${c.id}`}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))
        )}
      </div>

      {selected ? (
        <div className="cv-style-edit">
          <div className="cv-style-edit__hint">
            编辑字幕样式：{selected.text.slice(0, 16) || "（空文本）"}
          </div>
          <CaptionGeometryEditor caption={selected} />
          <FontStylePanel captionId={selectedId} />
          <CaptionAnimationSection caption={selected} />
          <Row>
            <Field label="字号">
              <TextInput
                type="number"
                step="1"
                min="1"
                value={form.fontSize}
                aria-label="字幕字号"
                onChange={(e) => applyStyle({ fontSize: e.target.value })}
              />
            </Field>
            <Field label="描边宽度">
              <TextInput
                type="number"
                step="1"
                min="0"
                value={form.strokeWidth}
                aria-label="描边宽度"
                onChange={(e) => applyStyle({ strokeWidth: e.target.value })}
              />
            </Field>
          </Row>
          <Field label="文字颜色">
            <input
              type="color"
              className="cv-input"
              value={form.color}
              aria-label="字幕颜色"
              onChange={(e) => applyStyle({ color: e.target.value })}
            />
          </Field>
          <Field label="对齐">
            <div role="group" aria-label="字幕对齐" className="cv-row cv-style-edit__align">
              <Button
                variant={form.align === "left" ? "primary" : "ghost"}
                size="sm"
                aria-label="左对齐"
                onClick={() => applyStyle({ align: "left" })}
              >
                <AlignLeft size={14} />
              </Button>
              <Button
                variant={form.align === "center" ? "primary" : "ghost"}
                size="sm"
                aria-label="居中"
                onClick={() => applyStyle({ align: "center" })}
              >
                <AlignCenter size={14} />
              </Button>
              <Button
                variant={form.align === "right" ? "primary" : "ghost"}
                size="sm"
                aria-label="右对齐"
                onClick={() => applyStyle({ align: "right" })}
              >
                <AlignRight size={14} />
              </Button>
            </div>
          </Field>
          <Button
            variant={form.bold ? "primary" : "ghost"}
            size="sm"
            aria-label="字幕加粗"
            onClick={() => applyStyle({ bold: !form.bold })}
          >
            <Bold size={14} />
            加粗
          </Button>
        </div>
      ) : null}
    </Panel>
  );
}
