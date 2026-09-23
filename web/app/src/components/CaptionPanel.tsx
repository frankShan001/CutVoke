/** 字幕面板：添加（文本 + 起止秒→rational）、列出当前字幕、删除、样式编辑、SRT 导入/导出。
    数据来源：project.sequence.captions（caption.add/update/remove）。
    SRT 走专用 HTTP 端点：GET /projects/{id}/captions.srt、POST /projects/{id}/captions/import。 */

import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { Captions, Plus, Trash2, Upload, Download, AlignLeft, AlignCenter, AlignRight, Bold } from "lucide-react";
import { Button, Field, Panel, Row, TextInput } from "./ui";
import { useEditor, showError, type CaptionDraftPreview } from "../store/editor";
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

interface CaptionDraft {
  text: string;
  start: string;
  end: string;
}

export function CaptionPanel() {
  const { state, dispatch } = useEditor();
  const captions = state.project?.sequence.captions || [];
  const readOnly = Boolean(state.editLock);
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
  const [captionDraft, setCaptionDraft] = useState<CaptionDraft>({
    text: "",
    start: "0",
    end: "3",
  });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const selectedCaptionSnapshot = useRef<string | null>(null);
  const selectedCaptionDraftSnapshot = useRef<string | null>(null);
  const captionDraftPreviewRef = useRef<CaptionDraftPreview | null>(null);

  const invalid = (message: string) =>
    new ApiFailure({ status: 400, code: "INVALID_ARGUMENT", message });

  // 选中字幕切换或服务端实际改动当前字幕时，同步编辑表单。与“每次工程刷新
  // 都覆盖草稿”不同：不相关字幕的更新不会打断输入；但外部 Agent 改了当前字幕
  // 时，旧的人工草稿必须失效，不能在解锁后反向覆盖 Agent 的结果。
  useEffect(() => {
    if (!selectedId) {
      selectedCaptionSnapshot.current = null;
      selectedCaptionDraftSnapshot.current = null;
      captionDraftPreviewRef.current = null;
      dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
      return;
    }
    const c = captions.find((x) => x.id === selectedId);
    if (!c) {
      selectedCaptionSnapshot.current = null;
      selectedCaptionDraftSnapshot.current = null;
      captionDraftPreviewRef.current = null;
      dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
      setSelectedId(null);
      return;
    }
    const snapshot = JSON.stringify(c);
    if (selectedCaptionSnapshot.current === snapshot) return;
    const draftSnapshot = JSON.stringify([c.id, c.text, c.start, c.end]);
    setForm({
      fontSize: c.fontSize != null ? String(c.fontSize) : "",
      color: c.color || "#ffffff",
      strokeWidth: c.strokeWidth != null ? String(c.strokeWidth) : "",
      align: c.align || "center",
      bold: c.bold || false,
    });
    // 样式/位置更新不应冲掉仍在编辑的文字草稿；仅当文本或时码真的变更
    // （例如 Agent 改了同一条字幕）时，才用工程值替换本地草稿。
    if (selectedCaptionDraftSnapshot.current !== draftSnapshot) {
      captionDraftPreviewRef.current = null;
      dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
      setCaptionDraft({
        text: c.text,
        start: String(rationalToSecs(c.start)),
        end: String(rationalToSecs(c.end)),
      });
      selectedCaptionDraftSnapshot.current = draftSnapshot;
    }
    selectedCaptionSnapshot.current = snapshot;
  }, [captions, selectedId, dispatch]);

  useEffect(() => () => {
    captionDraftPreviewRef.current = null;
    dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
  }, [dispatch]);

  const selected = selectedId ? captions.find((c) => c.id === selectedId) || null : null;
  const captionDraftChanged = selected != null && (
    captionDraft.text.trim() !== selected.text
    || captionDraft.start !== String(rationalToSecs(selected.start))
    || captionDraft.end !== String(rationalToSecs(selected.end))
  );

  const updateCaptionDraft = (next: CaptionDraft) => {
    setCaptionDraft(next);
    if (!selected || !state.currentId || readOnly) {
      captionDraftPreviewRef.current = null;
      dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
      return;
    }
    const changed = next.text.trim() !== selected.text
      || next.start !== String(rationalToSecs(selected.start))
      || next.end !== String(rationalToSecs(selected.end));
    if (!changed) {
      captionDraftPreviewRef.current = null;
      dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
      return;
    }
    const previewStart = trySecsToRational(next.start);
    const previewEnd = trySecsToRational(next.end);
    const rangeValid = previewStart && previewEnd
      && rationalToSecs(previewEnd) > rationalToSecs(previewStart);
    const preview: CaptionDraftPreview = {
      projectId: state.currentId,
      captionId: selected.id,
      text: next.text,
      start: rangeValid ? previewStart : selected.start,
      end: rangeValid ? previewEnd : selected.end,
    };
    captionDraftPreviewRef.current = preview;
    dispatch({
      type: "CAPTION_DRAFT_PREVIEW_SET",
      preview,
    });
  };

  // 租约结束后恢复仍然有效的本地草稿预览；若 Agent 改过这条字幕，上面的
  // 工程同步 effect 已清空草稿与 ref，不会把旧文本重新盖回画布。
  useEffect(() => {
    const preview = captionDraftPreviewRef.current;
    if (!readOnly && preview?.projectId === state.currentId) {
      dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview });
    }
  }, [readOnly, state.currentId, dispatch]);

  const selectCaption = (captionId: string) => {
    if (selected?.id === captionId) {
      // 重复点当前项只回到字幕起点，不清掉正在预览的本地草稿。
      const start = captionDraftChanged
        ? trySecsToRational(captionDraft.start) || selected.start
        : selected.start;
      dispatch({ type: "PLAYHEAD_SET", t: rationalToSecs(start) });
      return;
    }
    if (selected && selected.id !== captionId && captionDraftChanged) {
      dispatch({
        type: "STATUS_SET",
        severity: "warn",
        text: "当前字幕有未保存的文字或时间修改，请先保存",
      });
      return;
    }
    const caption = captions.find((item) => item.id === captionId);
    if (!caption) return;
    const draftSnapshot = JSON.stringify([
      caption.id,
      caption.text,
      caption.start,
      caption.end,
    ]);
    selectedCaptionSnapshot.current = JSON.stringify(caption);
    selectedCaptionDraftSnapshot.current = draftSnapshot;
    setForm({
      fontSize: caption.fontSize != null ? String(caption.fontSize) : "",
      color: caption.color || "#ffffff",
      strokeWidth: caption.strokeWidth != null ? String(caption.strokeWidth) : "",
      align: caption.align || "center",
      bold: caption.bold || false,
    });
    setCaptionDraft({
      text: caption.text,
      start: String(rationalToSecs(caption.start)),
      end: String(rationalToSecs(caption.end)),
    });
    captionDraftPreviewRef.current = null;
    dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
    dispatch({ type: "PLAYHEAD_SET", t: rationalToSecs(caption.start) });
    setSelectedId(captionId);
  };

  const beginNewCaption = () => {
    if (captionDraftChanged) {
      dispatch({
        type: "STATUS_SET",
        severity: "warn",
        text: "当前字幕有未保存的修改，请先保存或放弃后再新建",
      });
      return;
    }
    captionDraftPreviewRef.current = null;
    dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
    setSelectedId(null);
  };

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
    if (selectedId === id) {
      captionDraftPreviewRef.current = null;
      dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
    }
    void removeCaption(dispatch, state, id).then((res) => {
      if (res.ok && selectedId === id) setSelectedId(null);
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
    if (patch.color !== undefined && next.color !== (caption.color || "#ffffff")) {
      update.color = next.color;
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
    if (Object.keys(update).length > 1) void updateCaption(dispatch, state, update);
  };

  const setStyleField = (patch: Partial<StyleForm>) => {
    setForm((current) => ({ ...current, ...patch }));
  };

  const commitStyleOnEnter = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      e.currentTarget.blur();
    }
  };

  /** 把文字与起止时间合并成一次 caption.update，避免微调产生多条版本历史。 */
  const commitCaptionDraft = () => {
    if (!selected) return;
    const nextText = captionDraft.text.trim();
    if (!nextText) {
      showError(dispatch, invalid("字幕文本不能为空"));
      return;
    }
    const nextStart = trySecsToRational(captionDraft.start);
    const nextEnd = trySecsToRational(captionDraft.end);
    if (!nextStart || !nextEnd) {
      showError(dispatch, invalid("起止时间须为秒数字，如 1.5 / 3"));
      return;
    }
    if (rationalToSecs(nextEnd) <= rationalToSecs(nextStart)) {
      showError(dispatch, invalid("结束时间必须大于开始时间"));
      return;
    }

    const update: Parameters<typeof updateCaption>[2] = { captionId: selected.id };
    if (nextText !== selected.text) update.text = nextText;
    if (nextStart.num !== selected.start.num || nextStart.den !== selected.start.den) {
      update.start = nextStart;
    }
    if (nextEnd.num !== selected.end.num || nextEnd.den !== selected.end.den) {
      update.end = nextEnd;
    }
    if (Object.keys(update).length === 1) {
      // 例如只输入后又删回原值、或只留下首尾空格：没有工程改动，
      // 但本地草稿仍应回到规范形，不能继续阻止用户切换字幕。
      setCaptionDraft({
        text: nextText,
        start: String(rationalToSecs(nextStart)),
        end: String(rationalToSecs(nextEnd)),
      });
      captionDraftPreviewRef.current = null;
      dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
      dispatch({ type: "STATUS_SET", severity: "ok", text: "字幕没有改动" });
      return;
    }
    void updateCaption(dispatch, state, update).then((res) => {
      if (res.ok) {
        captionDraftPreviewRef.current = null;
        dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
        setCaptionDraft({
          text: nextText,
          start: String(rationalToSecs(nextStart)),
          end: String(rationalToSecs(nextEnd)),
        });
      }
    });
  };

  const discardCaptionDraft = () => {
    if (!selected) return;
    captionDraftPreviewRef.current = null;
    dispatch({ type: "CAPTION_DRAFT_PREVIEW_SET", preview: null });
    setCaptionDraft({
      text: selected.text,
      start: String(rationalToSecs(selected.start)),
      end: String(rationalToSecs(selected.end)),
    });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已放弃未保存的字幕修改" });
  };

  // ---- SRT 导入/导出 ----
  const rejectWhileAgentEditing = () => {
    if (!state.editLock) return false;
    dispatch({
      type: "STATUS_SET",
      severity: "warn",
      text: `Agent ${state.editLock.owner} 正在编辑，页面暂时只读`,
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
          <Button variant="secondary" size="sm" onClick={handleImportClick} aria-label="导入SRT" disabled={readOnly}>
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

      {selected ? (
        <Button
          variant="secondary"
          size="sm"
          full
          onClick={beginNewCaption}
          disabled={readOnly}
          style={{ marginTop: 8 }}
        >
          <Plus size={14} />
          新建字幕
        </Button>
      ) : (
        <>
          <Field label="字幕文本">
            <TextInput value={text} onChange={(e) => setText(e.target.value)} placeholder="如：开场字幕" disabled={readOnly} />
          </Field>
          <div style={{ marginTop: 8 }}>
            <Row>
              <Field label="开始 (s)">
                <TextInput type="number" step="any" value={start} onChange={(e) => setStart(e.target.value)} disabled={readOnly} />
              </Field>
              <Field label="结束 (s)">
                <TextInput type="number" step="any" value={end} onChange={(e) => setEnd(e.target.value)} disabled={readOnly} />
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
          <Button variant="primary" full onClick={handleAdd} style={{ marginTop: 8 }} disabled={readOnly}>
            <Plus size={14} />
            添加字幕
          </Button>
        </>
      )}

      <div className="cv-tracks" style={{ marginTop: 10 }}>
        {captions.length === 0 ? (
          <span className="cv-empty cv-empty--tight">暂无字幕</span>
        ) : (
          captions.map((c) => (
            <div
              key={c.id}
              className={`cv-caption-row${c.id === selectedId ? " cv-caption-row--active" : ""}`}
              onClick={() => selectCaption(c.id)}
              role="button"
              tabIndex={0}
              aria-label={`选择字幕 ${c.id}`}
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
            编辑字幕：{selected.text.slice(0, 16) || "（空文本）"}
          </div>
          <Field label="字幕文字">
            <textarea
              className="cv-input"
              rows={3}
              value={captionDraft.text}
              aria-label="编辑字幕文字"
              disabled={readOnly}
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
            <Field label="开始 (s)">
              <TextInput
                type="number"
                step="any"
                value={captionDraft.start}
                aria-label="编辑字幕开始时间"
                disabled={readOnly}
                onChange={(e) => updateCaptionDraft({ ...captionDraft, start: e.target.value })}
              />
            </Field>
            <Field label="结束 (s)">
              <TextInput
                type="number"
                step="any"
                value={captionDraft.end}
                aria-label="编辑字幕结束时间"
                disabled={readOnly}
                onChange={(e) => updateCaptionDraft({ ...captionDraft, end: e.target.value })}
              />
            </Field>
          </Row>
          <Button variant="primary" size="sm" full onClick={commitCaptionDraft} disabled={readOnly}>
            保存字幕文字与时间
          </Button>
          {captionDraftChanged ? (
            <Button variant="secondary" size="sm" full onClick={discardCaptionDraft}>
              放弃未保存修改
            </Button>
          ) : null}
          <span className="cv-hint">
            {readOnly && captionDraftChanged
              ? "Agent 编辑中，画布显示工程版本；未保存草稿已保留，解锁后继续预览。"
              : captionDraftChanged
                ? "画布正在预览未保存草稿；保存后写入工程。"
                : "可一次改文字和时码；文字框按 Ctrl/⌘ + Enter 也可保存。"}
          </span>
          <CaptionGeometryEditor caption={selected} disabled={readOnly} />
          <FontStylePanel captionId={selectedId} disabled={readOnly} />
          <CaptionAnimationSection caption={selected} disabled={readOnly} />
          <Row>
            <Field label="字号">
              <TextInput
                type="number"
                step="1"
                min="1"
                value={form.fontSize}
                aria-label="字幕字号"
                disabled={readOnly}
                onChange={(e) => setStyleField({ fontSize: e.target.value })}
                onBlur={() => commitStyle({ fontSize: form.fontSize })}
                onKeyDown={commitStyleOnEnter}
              />
            </Field>
            <Field label="描边宽度">
              <TextInput
                type="number"
                step="1"
                min="0"
                value={form.strokeWidth}
                aria-label="描边宽度"
                disabled={readOnly}
                onChange={(e) => setStyleField({ strokeWidth: e.target.value })}
                onBlur={() => commitStyle({ strokeWidth: form.strokeWidth })}
                onKeyDown={commitStyleOnEnter}
              />
            </Field>
          </Row>
          <Field label="文字颜色">
            <input
              type="color"
              className="cv-input"
              value={form.color}
              aria-label="字幕颜色"
              disabled={readOnly}
              onChange={(e) => commitStyle({ color: e.target.value })}
            />
          </Field>
          <Field label="对齐">
            <div role="group" aria-label="字幕对齐" className="cv-row cv-style-edit__align">
              <Button
                variant={form.align === "left" ? "primary" : "ghost"}
                size="sm"
                aria-label="左对齐"
                disabled={readOnly}
                onClick={() => commitStyle({ align: "left" })}
              >
                <AlignLeft size={14} />
              </Button>
              <Button
                variant={form.align === "center" ? "primary" : "ghost"}
                size="sm"
                aria-label="居中"
                disabled={readOnly}
                onClick={() => commitStyle({ align: "center" })}
              >
                <AlignCenter size={14} />
              </Button>
              <Button
                variant={form.align === "right" ? "primary" : "ghost"}
                size="sm"
                aria-label="右对齐"
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
            aria-label="字幕加粗"
            disabled={readOnly}
            onClick={() => commitStyle({ bold: !form.bold })}
          >
            <Bold size={14} />
            加粗
          </Button>
        </div>
      ) : null}
    </Panel>
  );
}
