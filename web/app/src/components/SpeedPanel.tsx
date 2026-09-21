/** 变速面板：预设倍速 + 倒放 toggle + 变速后时长显示。
    复用 clip.speed（speed 有理数/负数倒放），变速后时间线时长 = 原时长 / |speed|。 */

import { useMemo } from "react";
import { Gauge, RotateCcw } from "lucide-react";
import { Panel } from "./ui";
import { useEditor } from "../store/editor";
import { getLatestState } from "../store/actions";
import { setClipSpeed } from "../store/clipEdit";
import { rationalToSecs, secsToRational } from "../lib/rational";

const PRESETS = [0.25, 0.5, 1, 1.5, 2, 4];

export function SpeedPanel() {
  const { state, dispatch } = useEditor();
  const clip = useMemo(() => {
    if (!state.selection?.clipId || !state.project) return null;
    for (const t of state.project.sequence.tracks) {
      const c = t.clips.find((x) => x.id === state.selection?.clipId);
      if (c) return c;
    }
    return null;
  }, [state.project, state.selection]);
  if (!clip) {
    return (
      <Panel title="变速" subtitle="倍速与倒放">
        <p className="cv-empty">未选中片段。请在时间线选择要变速的片段。</p>
      </Panel>
    );
  }

  const currentSpeed = clip.speed ? rationalToSecs(clip.speed) : 1;
  const durSecs = rationalToSecs(clip.timelineEnd) - rationalToSecs(clip.timelineStart);
  const isReversed = currentSpeed < 0;
  const effDur = Math.abs(currentSpeed) > 0 ? durSecs / Math.abs(currentSpeed) : durSecs;

  const apply = (speed: number) => {
    const st = getLatestState() || state;
    void setClipSpeed(dispatch, st, { clipId: clip.id, speed: secsToRational(speed) });
  };

  return (
    <Panel title="变速" subtitle="倍速与倒放">
      <div className="cv-row">
        <Gauge size={14} className="cv-ic--video" />
        <span className="cv-field__label">当前倍速</span>
        <span className="cv-mono" style={{ marginLeft: "auto" }}>{isReversed ? "-" : ""}{Math.abs(currentSpeed).toFixed(2)}x</span>
      </div>
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 8 }}>
        {PRESETS.map((p) => (
          <button
            key={p}
            className={`cv-chip ${Math.abs(currentSpeed) === p && !isReversed ? "cv-chip--on" : ""}`}
            onClick={() => {
              apply(p);
            }}
          >
            {p}x
          </button>
        ))}
        <button
          className={`cv-chip ${isReversed ? "cv-chip--on" : ""}`}
          onClick={() => apply(isReversed ? Math.abs(currentSpeed) || 1 : -(Math.abs(currentSpeed) || 1))}
          title="倒放（负 speed）"
        >
          <RotateCcw size={11} /> 倒放
        </button>
      </div>
      <div style={{ marginTop: 8 }} className="inspector__row">
        <span className="inspector__key">源时长</span>
        <span className="inspector__val">{durSecs.toFixed(3)}s</span>
      </div>
      <div style={{ marginTop: 8 }} className="inspector__row">
        <span className="inspector__key">变速后时长</span>
        <span className="inspector__val">{effDur.toFixed(3)}s{isReversed ? "（倒放）" : ""}</span>
      </div>
      <p className="cv-hint" style={{ marginTop: 8 }}>
        变速后时间线总时长按原时长 / |speed| 变化；倒放为负 speed。
      </p>
    </Panel>
  );
}