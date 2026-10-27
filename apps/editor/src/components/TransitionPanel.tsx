import { tr, useLocale } from "../i18n";
/** 转场面板：选片段 → 选转场 + 时长 → 应用/切换；已有转场可移除。
 *  转场语义：挂在「后一段」片段上，做它与前一段之间的过渡。
 *  J01：转场类型从效果注册表派生（GET /effects?category=transition），不再硬编码清单。 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, Film, Trash2 } from "lucide-react";
import { Button, Field, Panel, Select, TextInput, Badge } from "./ui";
import { useEditor, showError, selectors } from "../store/editor";
import { stageEdit, releaseEdit, useInteractionProject } from "../store/interactive";
import { getLatestState } from "../store/actions";
import { removeEffect } from "../store/clipEdit";
import { rationalToSecs } from "../lib/rational";
import { findTransitionOnClip, transitionDurationSecs } from "../lib/transitions";
import {
  effectsByCategory,
  fetchResourcePreview,
  getEffectCatalog,
  type EffectSpec,
} from "../lib/effects";
import { paramControls } from "../lib/effectControls";
import { ParamControls } from "./inspector/ParamControls";
import { StructuredEffectControls, validStructuredParameters } from './inspector/StructuredEffectControls';
import type { Clip } from "../types/api";

const DURATION_PRESETS = [0.5, 1.0, 1.5, 2.0];

export function TransitionPanel() {
  const locale = useLocale();
  const { state, dispatch } = useEditor();
  const project = useInteractionProject(state.project);
  const tracks = project?.sequence.tracks || [];
  const selectedClipId = state.selection?.clipId ?? null;

  const [trackSel, setTrackSel] = useState("");
  const [clipSel, setClipSel] = useState("");
  const [transitions, setTransitions] = useState<EffectSpec[]>([]);
  const [transitionId, setTransitionId] = useState("");
  const [duration, setDuration] = useState("1.0");
  const [params, setParams] = useState<Record<string, unknown>>({});
  const busy = false;
  const [trial, setTrial] = useState<{ loading: boolean; url?: string; error?: string } | null>(null);
  const trialRequest = useRef<AbortController | null>(null);
  const trialUrl = useRef<string | null>(null);

  // 转场清单从注册表拉取（失败显式报错，不静默降级为空）
  useEffect(() => {
    let cancelled = false;
    getEffectCatalog()
      .then((all) => {
        if (cancelled) return;
        const list = effectsByCategory(all, "transition");
        setTransitions(list);
        setTransitionId((cur) => cur || list[0]?.effectId || "");
      })
      .catch((err) => {
        if (!cancelled) showError(dispatch, err);
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch, locale]);

  // 若 Inspector 选中了片段，同步到面板
  useEffect(() => {
    if (selectedClipId) {
      const tr = tracks.find((t) => t.clips.some((c) => c.id === selectedClipId));
      if (tr) {
        setTrackSel(tr.id);
        setClipSel(selectedClipId);
      }
    } else {
      setClipSel(""); setTrackSel("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedClipId]);

  const trackOptions = selectors.videoTracks(project);
  const clipOptions = useMemo(() => {
    const track = trackOptions.find((t) => t.id === trackSel);
    return track
      ? [...track.clips].sort((a, b) => rationalToSecs(a.timelineStart) - rationalToSecs(b.timelineStart))
      : [];
  }, [trackOptions, trackSel, locale]);

  const currentClip: Clip | null = useMemo(
    () => (clipSel ? clipOptions.find((c) => c.id === clipSel) || null : null),
    [clipOptions, clipSel, locale],
  );
  const selectedTrackLocked = !!tracks.find((track) => track.id === trackSel)?.locked;
  const readOnly = selectedTrackLocked || !!state.editLock;
  const currentFx = findTransitionOnClip(currentClip);
  const currentFxId = currentFx ? String(currentFx.effectId) : "";
  const currentFxDuration = currentFx ? transitionDurationSecs(currentFx) : 0;
  const selectedSpec = transitions.find((spec) => spec.effectId === transitionId);
  const defaultsKey = JSON.stringify(selectedSpec?.defaults || {});
  const currentParamsKey = JSON.stringify(currentFx?.params || {});
  // Auto-save changes revision and object identity without changing the image.
  // Invalidate a trial only when its effective inputs change.
  const trialScope = useMemo(() => JSON.stringify([state.currentId, clipSel, transitionId, duration, params,
    project?.sequence, project?.sequences?.filter(sequence => sequence.id !== project.sequence.id)]),
    [state.currentId, clipSel, transitionId, duration, params, project]);
  useEffect(() => {
    setParams(JSON.parse(transitionId === currentFxId ? currentParamsKey : defaultsKey));
  }, [clipSel, transitionId, currentFxId, currentParamsKey, defaultsKey]);
  const parameterControls = selectedSpec
    ? paramControls(selectedSpec, params).filter((control) => control.name !== "duration") : [];
  useEffect(() => {
    if (currentFxId) {
      setTransitionId(currentFxId);
      setDuration(String(currentFxDuration));
    }
  }, [clipSel, currentFxId, currentFxDuration]);

  useEffect(() => {
    trialRequest.current?.abort();
    if (trialUrl.current) URL.revokeObjectURL(trialUrl.current);
    trialUrl.current = null;
    setTrial(null);
    return () => {
      trialRequest.current?.abort();
      if (trialUrl.current) URL.revokeObjectURL(trialUrl.current);
      trialUrl.current = null;
    };
  }, [trialScope]);
  const transitionTarget = useMemo(() => {
    if (!currentClip) return { valid: false, reason: tr("请选择要添加转场的后一段片段"), maxDuration: 0 };
    const index = clipOptions.findIndex((clip) => clip.id === currentClip.id);
    if (index <= 0) return { valid: false, reason: tr("首个片段前没有可连接的片段"), maxDuration: 0 };
    const previous = clipOptions[index - 1];
    const gap = rationalToSecs(currentClip.timelineStart) - rationalToSecs(previous.timelineEnd);
    if (Math.abs(gap) > 0.001) return { valid: false, reason: tr("两段之间有 {0}s {1}，请先贴合再添加转场", Math.abs(gap).toFixed(3), gap > 0 ? tr("空隙") : tr("重叠")), maxDuration: 0 };
    const previousDuration = rationalToSecs(previous.timelineEnd) - rationalToSecs(previous.timelineStart);
    const currentDuration = rationalToSecs(currentClip.timelineEnd) - rationalToSecs(currentClip.timelineStart);
    const maxDuration = Math.min(5, previousDuration / 2, currentDuration / 2);
    if (maxDuration < 0.1) return { valid: false, reason: tr("相邻片段过短，无法形成至少 0.1 秒的转场"), maxDuration };
    return { valid: true, reason: tr("转场将连接此前一段与当前片段"), maxDuration };
  }, [currentClip, clipOptions, locale]);
  const requestedDuration = Number(duration);
  const durationSchema = selectedSpec?.parameters.properties?.duration;
  const minDuration = Math.max(.1, Number(durationSchema?.minimum ?? .1));
  const maxDuration = Math.min(5, Number(durationSchema?.maximum ?? 5));
  const validDuration = Number.isFinite(requestedDuration) && requestedDuration >= minDuration && requestedDuration <= maxDuration;
  const effectiveDuration = validDuration ? Math.min(requestedDuration, transitionTarget.maxDuration) : 0;
  const currentName = currentFx
    ? transitions.find((s) => s.effectId === String(currentFx.effectId))?.name ||
      String(currentFx.effectId)
    : "";

  const previewParams = (next: Record<string, unknown>, nextDuration = duration) => {
    if (!currentClip || !currentFx || readOnly) return;
    if(selectedSpec && !validStructuredParameters(selectedSpec,next))return;
    const d = Number(nextDuration);
    if (Number.isFinite(d) && d >= minDuration && d <= maxDuration && Object.values(next).every(v => v !== undefined && (typeof v !== "number" || Number.isFinite(v))))
      stageEdit(dispatch,`${currentClip.id}:${currentFxId}`,[{type:'effect.update',payload:{clipId:currentClip.id,effectId:currentFxId,
        params:{...next,duration:d},replaceParams:true}}]);
  };
  const changeDuration = (value: string) => { setDuration(value); previewParams(params, value); };

  const handleTrial = async () => {
    if (!state.currentId || !clipSel || !transitionId || !transitionTarget.valid) return;
    if (!validDuration) {
      setTrial({ loading: false, error: maxDuration === 5 ? tr("时长须在 0.1–5 秒之间") :
        `${tr("无效时长")} (${minDuration}–${maxDuration}s)` });
      return;
    }
    trialRequest.current?.abort();
    if (trialUrl.current) URL.revokeObjectURL(trialUrl.current);
    trialUrl.current = null;
    const controller = new AbortController();
    trialRequest.current = controller;
    setTrial({ loading: true });
    const result = await fetchResourcePreview(state.currentId, clipSel, transitionId,
                                              controller.signal, effectiveDuration, params);
    if (controller.signal.aborted) {
      if (result.kind === "frame") URL.revokeObjectURL(result.url);
      return;
    }
    if (result.kind === "frame") {
      trialUrl.current = result.url;
      setTrial({ loading: false, url: result.url });
    } else {
      setTrial({ loading: false,
        error: result.kind === "empty" ? tr("接缝处无画面") : result.message });
    }
  };

  const handleRemove = async () => {
    if (readOnly || !clipSel || !currentFx) return;
    const st = getLatestState() || state;
    await removeEffect(dispatch, st, { clipId: clipSel, effectId: String(currentFx.effectId) });
  };

  return (
    <Panel title={tr("转场参数")} subtitle={tr("调整当前片段的转场时长与参数")}>
      <Button full onClick={() => window.dispatchEvent(new CustomEvent("cutvoke:open-creative-domain", { detail: "transition" }))}>
        {tr("到左侧选择转场")}</Button>
      {readOnly ? (
        <p className="cv-hint" role="status">
          {state.editLock ? tr("Agent 正在编辑，转场暂不可修改。") : tr("目标片段所在轨道已锁定，解锁后可编辑转场。")}
        </p>
      ) : null}
      <Field label={tr("轨道")}>
        <Select
          value={trackSel}
          onChange={(e) => {
            setTrackSel(e.target.value);
            setClipSel("");
          }}
        >
          <option value="">{trackOptions.length ? tr("选择视频轨道…") : tr("（先添加视频轨道）")}</option>
          {trackOptions.map((t) => (
            <option key={t.id} value={t.id}>{t.id}（{t.clips.length}{tr(" 片段）")}</option>
          ))}
        </Select>
      </Field>
      <Field label={tr("后一段片段（转场挂此段）")}>
        <Select value={clipSel} onChange={(e) => {
          const clipId = e.target.value;
          setClipSel(clipId);
          if (clipId) dispatch({ type: "SELECTION_SET", selection: { trackId: trackSel, clipId } });
        }}>
          <option value="">
            {clipOptions.length ? tr("选择片段…") : tr("（该轨道无片段）")}
          </option>
          {clipOptions.map((c) => (
            <option key={c.id} value={c.id}>{c.id}</option>
          ))}
        </Select>
      </Field>

      {currentFx ? (
        <div style={{ margin: "8px 0" }}>
          <span className="cv-hint" style={{ marginTop: 0, display: "block", marginBottom: 4 }}>{tr("当前转场：")}</span>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Badge tone="info">{currentName}</Badge>
            <span className="cv-mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
              {transitionTarget.valid
                ? tr("{0}s 实际生效", Math.min(currentFxDuration, transitionTarget.maxDuration).toFixed(2))
                : tr("接缝已断开 · 暂不生效")}
            </span>
            <button
              className="cv-btn cv-btn--sm cv-btn--danger"
              onClick={handleRemove}
              disabled={readOnly}
              title={tr("移除转场")}
              style={{ marginLeft: "auto" }}
            >
              <Trash2 size={12} />{tr(" 移除")}</button>
          </div>
        </div>
      ) : null}

      {clipSel ? (
        <p className={`cv-hint${transitionTarget.valid ? "" : " cv-hint--warn"}`} role="status">
          {transitionTarget.reason}
        </p>
      ) : null}

      {currentFx ? <>
      <div style={{ marginTop: 8 }}>
        <span className="cv-field__label">{tr("时长（秒）")}</span>
        <div style={{ display: "flex", gap: 4, marginTop: 4, alignItems: "center" }}>
          {DURATION_PRESETS.map((d) => (
            <button
              key={d}
              className={`cv-chip ${duration === String(d) ? "cv-chip--on" : ""}`}
              onClick={() => changeDuration(String(d))}
              disabled={d < minDuration || d > maxDuration}
            >
              {d}s
            </button>
          ))}
        </div>
        <div style={{ marginTop: 6 }}>
          <TextInput type="number" step="0.1" min={minDuration} max={maxDuration} value={duration} onChange={(e) => changeDuration(e.target.value)} />
        </div>
        {transitionTarget.valid ? (
          <p className="cv-hint" role="status">{tr("最长可生效 ")}{transitionTarget.maxDuration.toFixed(2)}{tr("s；当前填写 ")}{validDuration ? tr("{0}s，实际 {1}s", requestedDuration.toFixed(2), effectiveDuration.toFixed(2)) : tr("无效时长")}{tr("。 转场从切点开始，不压短时间线；前段末帧作为过渡把手，不额外读取源素材。")}</p>
        ) : null}
      </div>

      {parameterControls.length ? <fieldset disabled={readOnly || busy}
        aria-label={selectedSpec?.name} style={{ border: 0, padding: 0, margin: "10px 0" }}>
        <ParamControls controls={parameterControls} onChange={(name, value) => {
          const next = { ...params, [name]: value }; setParams(next); previewParams(next);
        }} onCommit={() => releaseEdit(`${clipSel}:${currentFxId}`)} />
        {selectedSpec ? <StructuredEffectControls spec={selectedSpec} params={params} onChange={setParams} onCommit={next=>previewParams(next)}/> : null}
      </fieldset> : null}

      <Button full onClick={() => void handleTrial()}
        disabled={!clipSel || !transitionId || !transitionTarget.valid || !validDuration}
        style={{ marginTop: 10 }}>
        <Eye size={14} />{tr(" 预览当前转场")}</Button>
      {trial ? (
        <div className="resource-preview" role="status" aria-live="polite" style={{ marginTop: 8 }}>
          {trial.loading ? tr("正在渲染接缝画面…") : null}
          {trial.url ? <img src={trial.url} alt={tr("当前转场与时长在两段片段之间的真实预览帧")} /> : null}
          {trial.error ? <p className="resource-preview__error">{trial.error}</p> : null}
        </div>
      ) : null}

      <Button
        variant="primary"
        full
        onClick={() => { releaseEdit(`${clipSel}:${currentFxId}`); }}
        disabled={busy || readOnly || !clipSel || !transitionId || !transitionTarget.valid || !validDuration}
        style={{ marginTop: 10 }}
      >
        <Film size={14} />
        {busy ? tr("应用中…") : tr("保存转场参数")}
      </Button>
      <p className="cv-hint">{tr("转场挂在这段上与它前一相邻片段之间。切换转场是一次操作，可以一步撤销。")}</p>
      </> : <p className="cv-hint">{tr("当前片段还没有转场，请先在左侧转场库选择并应用。")}</p>}
    </Panel>
  );
}
