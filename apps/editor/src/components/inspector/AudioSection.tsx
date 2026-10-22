import { msg, tr, useLocale } from "../../i18n";
/** 片段音频小节（J01 拆分自 Inspector.tsx）：音量 / 淡入 / 淡出。
 *  拖动/输入只改本地态，提交时才发命令，避免命令风暴刷爆 revision。 */
import { useEffect, useRef, useState } from "react";
import { Volume2 } from "lucide-react";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import { stageEdit, releaseEdit } from "../../store/interactive";
import { probeMedia } from "../../lib/mediaApi";
import { rationalToSecs } from "../../lib/rational";
import type { Clip, Rational } from "../../types/api";

function valueOrDefault(value: Rational | undefined, fallback: number): number {
  return value ? rationalToSecs(value) : fallback;
}

export function AudioSection({ clip, canDetach = false, detachedAudioClipId }: {
  clip: Clip; canDetach?: boolean; detachedAudioClipId?: string;
}) {
  useLocale();
  const { state, dispatch } = useEditor();
  const holding = useRef(false);
  const [volPct, setVolPct] = useState(100);
  const [fadeInSec, setFadeInSec] = useState(0);
  const [fadeOutSec, setFadeOutSec] = useState(0);
  const [hasEmbeddedAudio, setHasEmbeddedAudio] = useState<boolean | null>(null);
  const [probeError, setProbeError] = useState("");
  const [probeAttempt, setProbeAttempt] = useState(0);
  const [detaching, setDetaching] = useState(false);

  useEffect(() => {
    setVolPct(Math.round(valueOrDefault(clip.volume, 1) * 100));
    setFadeInSec(valueOrDefault(clip.fadeIn, 0));
    setFadeOutSec(valueOrDefault(clip.fadeOut, 0));
  }, [clip.id, clip.volume, clip.fadeIn, clip.fadeOut]);

  useEffect(() => {
    if (!canDetach || detachedAudioClipId || !clip.assetRef.sourcePath) {
      setHasEmbeddedAudio(null);
      setProbeError("");
      return;
    }
    let cancelled = false;
    setHasEmbeddedAudio(null);
    setProbeError("");
    void probeMedia(clip.assetRef.sourcePath).then((result) => {
      if (cancelled) return;
      if (result.kind === "ok") setHasEmbeddedAudio(result.data.has_audio && result.data.has_video);
      else setProbeError(result.message);
    });
    return () => { cancelled = true; };
  }, [clip.assetRef.sourcePath, canDetach, detachedAudioClipId, probeAttempt]);

  const audioControlsDisabled = canDetach && (Boolean(detachedAudioClipId) || hasEmbeddedAudio !== true);
  let audioUnavailableReason = "";
  if (canDetach) {
    if (detachedAudioClipId) audioUnavailableReason = tr("原声已分离，请在关联音轨调整音量与淡入淡出。");
    else if (hasEmbeddedAudio === true) audioUnavailableReason = "";
    else if (probeError) audioUnavailableReason = tr("无法检查素材原声（{0}），音频参数暂不可用。", probeError);
    else if (!clip.assetRef.sourcePath) audioUnavailableReason = tr("素材路径不可用，无法检查原声。");
    else if (hasEmbeddedAudio === false) audioUnavailableReason = tr("素材不含原声，音量与淡入淡出不可用。");
    else audioUnavailableReason = tr("正在检查素材原声，检查完成前音频参数不可用。");
  }
  const audioStatusId = audioUnavailableReason ? `clip-audio-status-${clip.id}` : undefined;

  // 提交音频：合并发送 volume/fadeIn/fadeOut（拖动/输入只改本地态，这里才发命令）
  const commitAudio = (vol?: number, fi?: number, fo?: number) => {
    if (audioControlsDisabled) return;
    const volume = vol ?? volPct / 100;
    const fadeIn = fi ?? fadeInSec;
    const fadeOut = fo ?? fadeOutSec;
    if (Math.abs(volume - valueOrDefault(clip.volume, 1)) < 0.0001 &&
        Math.abs(fadeIn - valueOrDefault(clip.fadeIn, 0)) < 0.0001 &&
        Math.abs(fadeOut - valueOrDefault(clip.fadeOut, 0)) < 0.0001) return;
    if (![volume, fadeIn, fadeOut].every(Number.isFinite) || volume < 0 || fadeIn < 0 || fadeOut < 0) return;
    stageEdit(dispatch, `audio:${clip.id}`, [{ type: "clip.audio", payload: { clipId: clip.id, volume, fadeIn, fadeOut } }], holding.current);
  };

  const detachAudio = async () => {
    if (detaching || hasEmbeddedAudio !== true) return;
    setDetaching(true);
    try {
      const ctx = getLatestState() || state;
      const result = await runCommand(dispatch, ctx, "clip.detachAudio", { clipId: clip.id });
      if (!result.ok) return;
      const created = result.command?.changedEntities || [];
      const trackId = created.find((item) => item.type === "track" && item.change === "created")?.id;
      const clipId = created.find((item) => item.type === "clip" && item.change === "created")?.id;
      if (trackId && clipId) dispatch({ type: "SELECTION_SET", selection: { trackId, clipId } });
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("原声已分离到独立音轨，可单独移动和调音量；一次撤销可恢复") });
    } finally {
      setDetaching(false);
    }
  };

  return (
    <div className="inspector__audio">
      <div className="inspector__subrow">
        <span className="inspector__key">
          <Volume2 size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />{tr("音频")}</span>
        <span className="inspector__val">{volPct}%</span>
      </div>
      {audioUnavailableReason ? <p id={audioStatusId} className="cv-hint" role="status" style={{ margin: "0 0 8px" }}>
        {audioUnavailableReason}
        {canDetach && probeError && !detachedAudioClipId ? <button
          className="cv-btn cv-btn--sm cv-btn--secondary"
          type="button"
          style={{ marginLeft: 6 }}
          onClick={() => setProbeAttempt((attempt) => attempt + 1)}
        >{tr("重试检查")}</button> : null}
      </p> : null}
      <div className="inspector__audiorow">
        <input
          type="range"
          min={0}
          max={200}
          step={1}
          value={volPct}
          aria-label={tr("片段音量")}
          aria-disabled={audioControlsDisabled}
          aria-describedby={audioStatusId}
          className="cv-range"
          disabled={audioControlsDisabled}
          onPointerDown={() => { holding.current = true; }}
          onChange={(e) => { const n = Number(e.target.value); setVolPct(n); commitAudio(n / 100); }}
          onPointerUp={() => { holding.current = false; releaseEdit(`audio:${clip.id}`); }}
          onPointerCancel={() => { holding.current = false; releaseEdit(`audio:${clip.id}`); }}
          onKeyUp={(e) => {
            if (
              ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)
            ) {
              releaseEdit(`audio:${clip.id}`);
            }
          }}
        />
      </div>
      {canDetach ? <div className="inspector__subrow">
        <span className="inspector__key">{tr("原声")}</span>
        {detachedAudioClipId ? <span className="cv-hint">{tr("已分离到关联音轨")}</span> : <>
          <button className="cv-btn cv-btn--sm cv-btn--secondary" type="button"
            aria-describedby={audioStatusId}
            disabled={hasEmbeddedAudio !== true || detaching} onClick={() => { void detachAudio(); }}>
            {detaching ? tr("分离中…") : tr("分离原声")}
          </button>
        </>}
      </div> : null}
      <div className="inspector__subrow">
          <span className="inspector__key">{tr("淡入")}</span>
          <input
          type="number"
          step="0.1"
          min="0"
          value={fadeInSec}
            aria-label={tr("淡入时长")}
            aria-describedby={audioStatusId}
            disabled={audioControlsDisabled}
          className="cv-input"
          style={{ width: 56, height: 26 }}
          onChange={(e) => {
            const raw = e.target.value;
            const v = parseFloat(raw);
            setFadeInSec(raw === "" || isNaN(v) ? 0 : v);
            if (raw !== "") commitAudio(undefined, v);
          }}
          onBlur={() => releaseEdit(`audio:${clip.id}`)}
        />
        <span className="inspector__unit">{tr("秒")}</span>
          <span className="inspector__key">{tr("淡出")}</span>
          <input
          type="number"
          step="0.1"
          min="0"
          value={fadeOutSec}
            aria-label={tr("淡出时长")}
            aria-describedby={audioStatusId}
            disabled={audioControlsDisabled}
          className="cv-input"
          style={{ width: 56, height: 26 }}
          onChange={(e) => {
            const raw = e.target.value;
            const v = parseFloat(raw);
            setFadeOutSec(raw === "" || isNaN(v) ? 0 : v);
            if (raw !== "") commitAudio(undefined, undefined, v);
          }}
          onBlur={() => releaseEdit(`audio:${clip.id}`)}
        />
        <span className="inspector__unit">{tr("秒")}</span>
      </div>
    </div>
  );
}
