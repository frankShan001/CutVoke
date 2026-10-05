/** 转场面板：选片段 → 选转场 + 时长 → 应用/切换；已有转场可移除。
 *  转场语义：挂在「后一段」片段上，做它与前一段之间的过渡。
 *  J01：转场类型从效果注册表派生（GET /effects?category=transition），不再硬编码清单。 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, Film, Trash2 } from "lucide-react";
import { Button, Field, Panel, Select, TextInput, Badge } from "./ui";
import { useEditor, showError, selectors } from "../store/editor";
import { getLatestState } from "../store/actions";
import { applyTransition, removeEffect } from "../store/clipEdit";
import { rationalToSecs } from "../lib/rational";
import { findTransitionOnClip, transitionDurationSecs } from "../lib/transitions";
import {
  effectsByCategory,
  fetchResourcePreview,
  getEffectCatalog,
  iconForCategory,
  type EffectSpec,
} from "../lib/effects";
import { ApiFailure } from "../lib/api";
import type { Clip } from "../types/api";

const DURATION_PRESETS = [0.5, 1.0, 1.5, 2.0];

export function TransitionPanel() {
  const { state, dispatch } = useEditor();
  const tracks = state.project?.sequence.tracks || [];
  const selectedClipId = state.selection?.clipId ?? null;

  const [trackSel, setTrackSel] = useState("");
  const [clipSel, setClipSel] = useState("");
  const [transitions, setTransitions] = useState<EffectSpec[]>([]);
  const [transitionId, setTransitionId] = useState("");
  const [duration, setDuration] = useState("1.0");
  const [busy, setBusy] = useState(false);
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
  }, [dispatch]);

  // 若 Inspector 选中了片段，同步到面板
  useEffect(() => {
    if (selectedClipId) {
      const tr = tracks.find((t) => t.clips.some((c) => c.id === selectedClipId));
      if (tr) {
        setTrackSel(tr.id);
        setClipSel(selectedClipId);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedClipId]);

  const trackOptions = selectors.videoTracks(state.project);
  const clipOptions = useMemo(() => {
    const track = trackOptions.find((t) => t.id === trackSel);
    return track
      ? [...track.clips].sort((a, b) => rationalToSecs(a.timelineStart) - rationalToSecs(b.timelineStart))
      : [];
  }, [trackOptions, trackSel]);

  const currentClip: Clip | null = useMemo(
    () => (clipSel ? clipOptions.find((c) => c.id === clipSel) || null : null),
    [clipOptions, clipSel],
  );
  const selectedTrackLocked = !!tracks.find((track) => track.id === trackSel)?.locked;
  const readOnly = selectedTrackLocked || !!state.editLock;
  const currentFx = findTransitionOnClip(currentClip);
  const currentFxId = currentFx ? String(currentFx.effectId) : "";
  const currentFxDuration = currentFx ? transitionDurationSecs(currentFx) : 0;
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
  }, [state.currentId, clipSel, transitionId, duration, state.project?.revision]);
  const transitionTarget = useMemo(() => {
    if (!currentClip) return { valid: false, reason: "请选择要添加转场的后一段片段", maxDuration: 0 };
    const index = clipOptions.findIndex((clip) => clip.id === currentClip.id);
    if (index <= 0) return { valid: false, reason: "首个片段前没有可连接的片段", maxDuration: 0 };
    const previous = clipOptions[index - 1];
    const gap = rationalToSecs(currentClip.timelineStart) - rationalToSecs(previous.timelineEnd);
    if (Math.abs(gap) > 0.001) return { valid: false, reason: `两段之间有 ${Math.abs(gap).toFixed(3)}s ${gap > 0 ? "空隙" : "重叠"}，请先贴合再添加转场`, maxDuration: 0 };
    const previousDuration = rationalToSecs(previous.timelineEnd) - rationalToSecs(previous.timelineStart);
    const currentDuration = rationalToSecs(currentClip.timelineEnd) - rationalToSecs(currentClip.timelineStart);
    const maxDuration = Math.min(5, previousDuration / 2, currentDuration / 2);
    if (maxDuration < 0.1) return { valid: false, reason: "相邻片段过短，无法形成至少 0.1 秒的转场", maxDuration };
    return { valid: true, reason: "转场将连接此前一段与当前片段", maxDuration };
  }, [currentClip, clipOptions]);
  const requestedDuration = Number(duration);
  const validDuration = Number.isFinite(requestedDuration) && requestedDuration >= 0.1 && requestedDuration <= 5;
  const effectiveDuration = validDuration ? Math.min(requestedDuration, transitionTarget.maxDuration) : 0;
  const currentName = currentFx
    ? transitions.find((s) => s.effectId === String(currentFx.effectId))?.name ||
      String(currentFx.effectId)
    : "";

  const invalid = (message: string) =>
    new ApiFailure({ status: 400, code: "INVALID_ARGUMENT", message });

  const handleTrial = async () => {
    if (!state.currentId || !clipSel || !transitionId || !transitionTarget.valid) return;
    const seconds = Number(duration);
    if (!validDuration) {
      setTrial({ loading: false, error: "时长须在 0.1–5 秒之间" });
      return;
    }
    trialRequest.current?.abort();
    if (trialUrl.current) URL.revokeObjectURL(trialUrl.current);
    trialUrl.current = null;
    const controller = new AbortController();
    trialRequest.current = controller;
    setTrial({ loading: true });
    const result = await fetchResourcePreview(state.currentId, clipSel, transitionId,
                                              controller.signal, seconds);
    if (controller.signal.aborted) {
      if (result.kind === "frame") URL.revokeObjectURL(result.url);
      return;
    }
    if (result.kind === "frame") {
      trialUrl.current = result.url;
      setTrial({ loading: false, url: result.url });
    } else {
      setTrial({ loading: false,
        error: result.kind === "empty" ? "接缝处无画面" : result.message });
    }
  };

  const handleApply = async () => {
    if (readOnly) return;
    if (!state.currentId) {
      showError(dispatch, invalid("请先选择工程"));
      return;
    }
    if (!clipSel) {
      showError(dispatch, invalid("请选择要添加转场的「后一段」片段"));
      return;
    }
    if (!transitionTarget.valid) {
      showError(dispatch, invalid(transitionTarget.reason));
      return;
    }
    if (!transitionId) {
      showError(dispatch, invalid("转场清单尚未就绪，请稍候重试"));
      return;
    }
    if (!validDuration) {
      showError(dispatch, invalid("转场时长须在 0.1–5 秒之间"));
      return;
    }
    setBusy(true);
    const st = getLatestState() || state;
    const res = await applyTransition(dispatch, st, {
      clipId: clipSel,
      effectId: transitionId,
      duration: Math.round(effectiveDuration * 1000) / 1000,
    });
    setBusy(false);
    if (res.ok && currentFx) {
      const name = transitions.find((s) => s.effectId === transitionId)?.name || transitionId;
      dispatch({ type: "STATUS_SET", severity: "ok", text: `已切换转场为 ${name}` });
    }
  };

  const handleRemove = async () => {
    if (readOnly || !clipSel || !currentFx) return;
    const st = getLatestState() || state;
    await removeEffect(dispatch, st, { clipId: clipSel, effectId: String(currentFx.effectId) });
  };

  return (
    <Panel title="转场" subtitle="挂在后一段片段上">
      {readOnly ? (
        <p className="cv-hint" role="status">
          {state.editLock ? "Agent 正在编辑，转场暂不可修改。" : "目标片段所在轨道已锁定，解锁后可编辑转场。"}
        </p>
      ) : null}
      <Field label="轨道">
        <Select
          value={trackSel}
          onChange={(e) => {
            setTrackSel(e.target.value);
            setClipSel("");
          }}
        >
          <option value="">{trackOptions.length ? "选择视频轨道…" : "（先添加视频轨道）"}</option>
          {trackOptions.map((t) => (
            <option key={t.id} value={t.id}>{t.id}（{t.clips.length} 片段）</option>
          ))}
        </Select>
      </Field>
      <Field label="后一段片段（转场挂此段）">
        <Select value={clipSel} onChange={(e) => {
          const clipId = e.target.value;
          setClipSel(clipId);
          if (clipId) dispatch({ type: "SELECTION_SET", selection: { trackId: trackSel, clipId } });
        }}>
          <option value="">
            {clipOptions.length ? "选择片段…" : "（该轨道无片段）"}
          </option>
          {clipOptions.map((c) => (
            <option key={c.id} value={c.id}>{c.id}</option>
          ))}
        </Select>
      </Field>

      {currentFx ? (
        <div style={{ margin: "8px 0" }}>
          <span className="cv-hint" style={{ marginTop: 0, display: "block", marginBottom: 4 }}>
            当前转场：
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Badge tone="info">{currentName}</Badge>
            <span className="cv-mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
              {transitionTarget.valid
                ? `${Math.min(currentFxDuration, transitionTarget.maxDuration).toFixed(2)}s 实际生效`
                : "接缝已断开 · 暂不生效"}
            </span>
            <button
              className="cv-btn cv-btn--sm cv-btn--danger"
              onClick={handleRemove}
              disabled={readOnly}
              title="移除转场"
              style={{ marginLeft: "auto" }}
            >
              <Trash2 size={12} /> 移除
            </button>
          </div>
        </div>
      ) : null}

      {clipSel ? (
        <p className={`cv-hint${transitionTarget.valid ? "" : " cv-hint--warn"}`} role="status">
          {transitionTarget.reason}
        </p>
      ) : null}

      <div style={{ marginTop: 8 }}>
        <span className="cv-field__label">转场类型</span>
        <div className="transition-options">
          {transitions.map((t) => {
            const Icon = iconForCategory(t.category);
            return (
              <button
                key={t.effectId}
                className={`cv-chip ${transitionId === t.effectId ? "cv-chip--on" : ""}`}
                style={{ justifyContent: "flex-start", width: "100%" }}
                onClick={() => setTransitionId(t.effectId)}
                title={t.description}
              >
                <Icon size={11} />
                {t.name}
              </button>
            );
          })}
          {transitions.length === 0 ? (
            <span className="cv-empty cv-empty--tight">转场清单加载中…</span>
          ) : null}
        </div>
      </div>

      <div style={{ marginTop: 8 }}>
        <span className="cv-field__label">时长（秒）</span>
        <div style={{ display: "flex", gap: 4, marginTop: 4, alignItems: "center" }}>
          {DURATION_PRESETS.map((d) => (
            <button
              key={d}
              className={`cv-chip ${duration === String(d) ? "cv-chip--on" : ""}`}
              onClick={() => setDuration(String(d))}
            >
              {d}s
            </button>
          ))}
        </div>
        <div style={{ marginTop: 6 }}>
          <TextInput type="number" step="0.1" min={0.1} max={5} value={duration} onChange={(e) => setDuration(e.target.value)} />
        </div>
        {transitionTarget.valid ? (
          <p className="cv-hint" role="status">
            最长可生效 {transitionTarget.maxDuration.toFixed(2)}s；当前填写 {validDuration ? `${requestedDuration.toFixed(2)}s，实际 ${effectiveDuration.toFixed(2)}s` : "无效时长"}。
            转场从切点开始，不压短时间线；前段末帧作为过渡把手，不额外读取源素材。
          </p>
        ) : null}
      </div>

      <Button full onClick={() => void handleTrial()}
        disabled={!clipSel || !transitionId || !transitionTarget.valid || !validDuration}
        style={{ marginTop: 10 }}>
        <Eye size={14} /> 预览当前转场
      </Button>
      {trial ? (
        <div className="resource-preview" role="status" aria-live="polite" style={{ marginTop: 8 }}>
          {trial.loading ? "正在渲染接缝画面…" : null}
          {trial.url ? <img src={trial.url} alt="当前转场与时长在两段片段之间的真实预览帧" /> : null}
          {trial.error ? <p className="resource-preview__error">{trial.error}</p> : null}
        </div>
      ) : null}

      <Button
        variant="primary"
        full
        onClick={handleApply}
        disabled={busy || readOnly || !clipSel || !transitionId || !transitionTarget.valid || !validDuration}
        style={{ marginTop: 10 }}
      >
        <Film size={14} />
        {busy ? "应用中…" : currentFx ? "切换转场" : "应用转场"}
      </Button>
      <p className="cv-hint">
        转场挂在这段上与它前一相邻片段之间。切换转场是一次操作，可以一步撤销。
      </p>
    </Panel>
  );
}
