/** 转场面板：选片段 → 选转场 + 时长 → 应用/切换；已有转场可移除。
 *  转场语义：挂在「后一段」片段上，做它与前一段之间的过渡。
 *  J01：转场类型从效果注册表派生（GET /effects?category=transition），不再硬编码清单。 */

import { useEffect, useMemo, useState } from "react";
import { Film, Trash2 } from "lucide-react";
import { Button, Field, Panel, Select, TextInput, Badge } from "./ui";
import { useEditor, showError, selectors } from "../store/editor";
import { getLatestState } from "../store/actions";
import { applyTransition, removeEffect } from "../store/clipEdit";
import { rationalToSecs } from "../lib/rational";
import { findTransitionOnClip, transitionDurationSecs } from "../lib/transitions";
import {
  effectsByCategory,
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
  const transitionTarget = useMemo(() => {
    if (!currentClip) return { valid: false, reason: "请选择要添加转场的后一段片段" };
    const index = clipOptions.findIndex((clip) => clip.id === currentClip.id);
    if (index <= 0) return { valid: false, reason: "首个片段前没有可连接的片段" };
    const previous = clipOptions[index - 1];
    const gap = rationalToSecs(currentClip.timelineStart) - rationalToSecs(previous.timelineEnd);
    if (Math.abs(gap) > 0.001) return { valid: false, reason: "两段之间有空隙，请先关闭间隙再添加转场" };
    return { valid: true, reason: "转场将连接此前一段与当前片段" };
  }, [currentClip, clipOptions]);
  const currentName = currentFx
    ? transitions.find((s) => s.effectId === String(currentFx.effectId))?.name ||
      String(currentFx.effectId)
    : "";

  const invalid = (message: string) =>
    new ApiFailure({ status: 400, code: "INVALID_ARGUMENT", message });

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
    const dur = parseFloat(duration);
    if (isNaN(dur) || dur <= 0) {
      showError(dispatch, invalid("转场时长须为正数（秒）"));
      return;
    }
    const durClamped = Math.min(5, Math.max(0.1, dur));
    setBusy(true);
    const st = getLatestState() || state;
    const res = await applyTransition(dispatch, st, {
      clipId: clipSel,
      effectId: transitionId,
      duration: Math.round(durClamped * 10) / 10,
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
        <Select value={clipSel} onChange={(e) => setClipSel(e.target.value)}>
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
              {transitionDurationSecs(currentFx).toFixed(1)}s
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
        <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
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
      </div>

      <Button
        variant="primary"
        full
        onClick={handleApply}
        disabled={busy || readOnly || !clipSel || !transitionId || !transitionTarget.valid}
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
