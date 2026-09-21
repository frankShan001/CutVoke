/** J12 模板库抽屉：浏览模板（按画幅/分类筛选）+ 一键套用到当前工程。
    落点双入口：编辑器顶栏「模板库」与工程首页「模板库」均打开本抽屉。
      - 编辑器内（currentId 存在）：套用到当前工程，clearExisting 默认 false（追加）。
      - 首页（currentId 为空）：先按模板画幅建工程，再套用并进入编辑器。
    套用成功后刷新工程快照，并用 template_applied 汇总事件提示占位素材情况。 */

import { useEffect, useMemo, useState } from "react";
import {
  LayoutTemplate,
  X,
  Music,
  Scissors,
  Layers,
  Clock,
  Captions,
  AlertTriangle,
  RefreshCw,
} from "lucide-react";
import { Badge, Button } from "./ui";
import { useEditor, showError } from "../store/editor";
import {
  applyTemplate as apiApply,
  listTemplates,
  type ListTemplatesParams,
} from "../lib/api";
import { createProject, refreshProject, getLatestState } from "../store/actions";
import { TemplateApplyDialog } from "./TemplateApplyDialog";
import type {
  Template,
  TemplateAppliedEvent,
  TemplateAspect,
} from "../types/api";

const ASPECT_DIM: Record<TemplateAspect, [number, number]> = {
  "16:9": [1920, 1080],
  "9:16": [1080, 1920],
  "1:1": [1080, 1080],
};

const ASPECT_OPTIONS: Array<TemplateAspect | "all"> = ["all", "16:9", "9:16", "1:1"];

const fmtSec = (s: number) => `${(Number.isInteger(s) ? s : s.toFixed(1))}秒`;

export function TemplateGallery() {
  const { state, dispatch } = useEditor();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [skipped, setSkipped] = useState<{ id?: string; name?: string; error?: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const [aspect, setAspect] = useState<TemplateAspect | "all">("all");
  const [category, setCategory] = useState<string>("all");

  const [confirming, setConfirming] = useState<Template | null>(null);
  const [clearExisting, setClearExisting] = useState(false);
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const [applyErr, setApplyErr] = useState<string | null>(null);

  const close = () => {
    dispatch({ type: "TEMPLATE_OPEN_SET", open: false });
    setConfirming(null);
    setApplyErr(null);
  };

  const load = async (params?: ListTemplatesParams) => {
    setLoading(true);
    setLoadErr(null);
    try {
      const res = await listTemplates(params);
      setTemplates(res.templates || []);
      setSkipped(res.skipped || []);
    } catch (err) {
      const f = showError(dispatch, err);
      setLoadErr(`${f.code} — ${f.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (confirming) setConfirming(null);
        else close();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const categories = useMemo(
    () => Array.from(new Set(templates.map((t) => t.category))),
    [templates],
  );

  const visible = useMemo(
    () =>
      templates.filter(
        (t) =>
          (aspect === "all" || t.aspect === aspect) &&
          (category === "all" || t.category === category),
      ),
    [templates, aspect, category],
  );
  const handleApply = async (tpl: Template) => {
    if (applyingId) return;
    setApplyErr(null);
    const latest = getLatestState() ?? state;
    let pid = latest.currentId ?? state.currentId;
    const createdHere = !pid;
    if (!pid) {
      const [w, h] = ASPECT_DIM[tpl.aspect];
      const created = await createProject(dispatch, {
        width: w,
        height: h,
        fps: 30,
        name: tpl.name,
      });
      if (!created) return; // createProject 已报错
      pid = created;
    }
    setApplyingId(tpl.id);
    try {
      const rev = getLatestState()?.revision || "";
      const res = await apiApply(pid, {
        templateId: tpl.id,
        clearExisting,
        expectedRevision: rev || undefined,
      });
      await refreshProject(dispatch, pid);
      const ev = res.changedEntities.find(
        (e): e is TemplateAppliedEvent => (e as { type?: string }).type === "template_applied",
      );
      if (ev) {
        const ph =
          ev.placeholderCount > 0
            ? `，${ev.placeholderCount} 个槽位使用内置占位素材`
            : "，素材已齐备";
        dispatch({
          type: "STATUS_SET",
          severity: "ok",
          text: `已套用模板《${ev.name}》：时长 ${fmtSec(ev.durationSec)}${ph}`,
        });
      } else {
        dispatch({ type: "STATUS_SET", severity: "ok", text: `已套用模板 ${tpl.name}` });
      }
      if (createdHere) {
        dispatch({ type: "PROJECT_NAME_SET", name: tpl.name });
        dispatch({ type: "VIEW_SET", view: "editor" });
      }
      close();
    } catch (err) {
      const f = showError(dispatch, err);
      setApplyErr(`${f.code} — ${f.message}`);
    } finally {
      setApplyingId(null);
    }
  };

  return (
    <div className="tpl-dialog" role="dialog" aria-modal="true" aria-label="模板库">
      <div className="tpl-dialog__backdrop" onClick={close} />
      <section className="tpl-dialog__panel">
        <header className="tpl-dialog__head">
          <h2 className="tpl-dialog__title">
            <LayoutTemplate size={16} /> 模板库
          </h2>
          <span className="tpl-dialog__hint">浏览工程模板，一键套用为完整时间线</span>
          <button className="tpl-dialog__close" onClick={close} aria-label="关闭模板库">
            <X size={16} />
          </button>
        </header>

        {skipped.length > 0 && (
          <div className="tpl-skip" role="alert">
            <AlertTriangle size={14} />
            <span>
              {skipped.length} 个模板加载失败（已跳过）：
              {skipped.map((s) => s.name || s.id || "未知").join("、")}
            </span>
          </div>
        )}

        <div className="tpl-filters">
          <div className="tpl-filters__group">
            <span className="tpl-filters__label">画幅</span>
            {ASPECT_OPTIONS.map((a) => (
              <button
                key={a}
                className={`tpl-chip ${aspect === a ? "tpl-chip--on" : ""}`}
                onClick={() => setAspect(a)}
                aria-pressed={aspect === a}
              >
                {a === "all" ? "全部" : a}
              </button>
            ))}
          </div>
          <div className="tpl-filters__group">
            <span className="tpl-filters__label">分类</span>
            <button
              className={`tpl-chip ${category === "all" ? "tpl-chip--on" : ""}`}
              onClick={() => setCategory("all")}
              aria-pressed={category === "all"}
            >
              全部
            </button>
            {categories.map((c) => (
              <button
                key={c}
                className={`tpl-chip ${category === c ? "tpl-chip--on" : ""}`}
                onClick={() => setCategory(c)}
                aria-pressed={category === c}
              >
                {c}
              </button>
            ))}
          </div>
        </div>

        <div className="tpl-body">
          {loading ? (
            <div className="tpl-state">
              <RefreshCw size={18} className="tpl-spin" />
              <p>正在加载模板…</p>
            </div>
          ) : loadErr ? (
            <div className="tpl-state tpl-state--err">
              <AlertTriangle size={18} />
              <p>模板加载失败：{loadErr}</p>
              <Button variant="secondary" size="sm" onClick={() => void load()}>
                <RefreshCw size={13} /> 重试
              </Button>
            </div>
          ) : visible.length === 0 ? (
            <div className="tpl-state">
              <Layers size={18} />
              <p>没有匹配的模板。换个画幅或分类筛选试试。</p>
            </div>
          ) : (
            <ul className="tpl-grid">
              {visible.map((t) => (
                <li key={t.id} className="tpl-card">
                  <div className="tpl-card__head">
                    <span className="tpl-card__name">{t.name}</span>
                    <Badge tone="info">{t.aspect}</Badge>
                  </div>
                  <p className="tpl-card__desc">{t.description}</p>
                  <div className="tpl-card__meta">
                    <span>
                      <Layers size={12} /> {t.slotCount} 槽位
                    </span>
                    <span>
                      <Clock size={12} /> {fmtSec(t.estimatedSeconds)}
                    </span>
                    <span>
                      <Captions size={12} /> {t.captionCount} 字幕
                    </span>
                  </div>
                  <div className="tpl-card__tags">
                    <Badge tone={t.hasBgm ? "ok" : "neutral"}>
                      <Music size={11} /> {t.hasBgm ? "含背景音乐" : "无背景音乐"}
                    </Badge>
                    <Badge tone={t.transition ? "video" : "neutral"}>
                      <Scissors size={11} /> {t.transition ? `转场 ${t.transition}` : "无转场"}
                    </Badge>
                  </div>
                  <button
                    className="cv-btn cv-btn--primary cv-btn--md cv-btn--full"
                    onClick={() => {
                      setConfirming(t);
                      setApplyErr(null);
                    }}
                    aria-label={`套用模板 ${t.name}`}
                  >
                    套用
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {confirming && (
          <TemplateApplyDialog
            template={confirming}
            inEditor={!!state.currentId}
            clearExisting={clearExisting}
            onClearExistingChange={setClearExisting}
            applying={applyingId === confirming.id}
            applyErr={applyErr}
            busyOther={!!applyingId && applyingId !== confirming.id}
            onConfirm={() => void handleApply(confirming)}
            onCancel={() => setConfirming(null)}
          />
        )}
      </section>
    </div>
  );
}
