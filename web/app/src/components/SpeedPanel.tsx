/** 恒速面板：以源范围为基准预演时长与结束点，再提交单次工程命令。 */

import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { Gauge, RotateCcw } from "lucide-react";
import { Panel } from "./ui";
import { useEditor } from "../store/editor";
import { getLatestState } from "../store/actions";
import { setClipCurve, setClipFrameInterpolation, setClipSpeed } from "../store/clipEdit";
import { rationalToSecs, secsToRational } from "../lib/rational";
import { curveElapsed, curvePoints, validCurvePoints, type SpeedPointDraft } from "../lib/speedCurve";
import { fmtTimePrecise } from "./timeline/util";

const PRESETS = [0.25, 0.5, 1, 1.5, 2, 4];
const MIN_SPEED = 0.1;
const MAX_SPEED = 8;
const CURVE_PRESETS: { label: string; speeds: number[] }[] = [
  { label: "渐快", speeds: [0.5, 0.65, 1, 1.5, 2] },
  { label: "渐慢", speeds: [2, 1.5, 1, 0.65, 0.5] },
  { label: "快慢快", speeds: [2, 1.2, 0.5, 1.2, 2] },
  { label: "慢快慢", speeds: [0.5, 1, 2, 1, 0.5] },
];
const defaultCurve = (speed: number): SpeedPointDraft[] =>
  [0, 0.25, 0.5, 0.75, 1].map((at) => ({ at, speed: Math.max(0.1, Math.min(8, speed)) }));

export function SpeedPanel() {
  const { state, dispatch } = useEditor();
  const selectedTrackLocked = !!state.project?.sequence.tracks.find((track) =>
    track.clips.some((item) => item.id === state.selection?.clipId),
  )?.locked;
  const readOnly = selectedTrackLocked || !!state.editLock;
  const selected = useMemo(() => {
    if (!state.selection?.clipId || !state.project) return null;
    for (const t of state.project.sequence.tracks) {
      const c = t.clips.find((x) => x.id === state.selection?.clipId);
      if (c) return { clip: c, track: t };
    }
    return null;
  }, [state.project, state.selection?.clipId]);
  const clip = selected?.clip;
  const currentSpeed = clip?.speed ? rationalToSecs(clip.speed) : 1;
  const currentPreservePitch = clip?.preservePitch ?? true;
  const storedCurve = clip?.speedCurve;
  const hasSlowMotion = storedCurve
    ? storedCurve.points.some((point) => rationalToSecs(point.speed) < 1)
    : Math.abs(currentSpeed) < 1;
  const frameInterpolation = clip?.frameInterpolation ?? "none";
  const [mode, setMode] = useState<"constant" | "curve">(storedCurve ? "curve" : "constant");
  const [curveDraft, setCurveDraft] = useState<SpeedPointDraft[]>(
    () => curvePoints(storedCurve) || defaultCurve(Math.abs(currentSpeed)));
  const dragIndex = useRef<number | null>(null);
  const [draftMagnitude, setDraftMagnitude] = useState(() => String(Math.abs(currentSpeed)));
  const [draftReversed, setDraftReversed] = useState(() => currentSpeed < 0);
  const [draftPreservePitch, setDraftPreservePitch] = useState(currentPreservePitch);
  useEffect(() => {
    setDraftMagnitude(String(Math.abs(currentSpeed)));
    setDraftReversed(currentSpeed < 0);
    setDraftPreservePitch(currentPreservePitch);
  }, [clip?.id, currentSpeed, currentPreservePitch]);
  const storedCurveKey = JSON.stringify(storedCurve);
  useEffect(() => {
    setCurveDraft(curvePoints(storedCurve) || defaultCurve(Math.abs(currentSpeed)));
    setMode(storedCurve ? "curve" : "constant");
  }, [clip?.id, storedCurveKey, currentSpeed]);
  if (!clip) {
    return (
      <Panel title="变速" subtitle="倍速与倒放">
        <p className="cv-empty">未选中片段。请在时间线选择要变速的片段。</p>
      </Panel>
    );
  }

  const timelineStart = rationalToSecs(clip.timelineStart);
  const durSecs = rationalToSecs(clip.timelineEnd) - rationalToSecs(clip.timelineStart);
  // timelineEnd already reflects current speed; multiplying by |speed| recovers
  // the consumed source range. Dividing timeline duration again was incorrect.
  const sourceDuration = storedCurve
    ? rationalToSecs(storedCurve.sourceDuration) : durSecs * Math.abs(currentSpeed);
  const draftValue = Number(draftMagnitude);
  const validSpeed = Number.isFinite(draftValue) && draftValue >= MIN_SPEED && draftValue <= MAX_SPEED;
  const effDur = validSpeed ? sourceDuration / draftValue : durSecs;
  const proposedEnd = timelineStart + effDur;
  const nextStart = selected!.track.clips
    .filter((item) => item.id !== clip.id && rationalToSecs(item.timelineStart) >= timelineStart - 1e-6)
    .reduce((nearest, item) => Math.min(nearest, rationalToSecs(item.timelineStart)), Infinity);
  const wouldOverlap = validSpeed && proposedEnd > nextStart + 1e-6;
  const targetSpeed = (draftReversed ? -1 : 1) * draftValue;
  const changed = validSpeed && (storedCurve != null ||
    Math.abs(targetSpeed - currentSpeed) > 1e-6 || draftPreservePitch !== currentPreservePitch);
  const validCurve = validCurvePoints(curveDraft);
  const curveDuration = validCurve ? curveElapsed(sourceDuration, sourceDuration, curveDraft) : durSecs;
  const curveEnd = timelineStart + curveDuration;
  const curveOverlap = validCurve && curveEnd > nextStart + 1e-6;
  const originalPoints = curvePoints(storedCurve);
  const curveChanged = JSON.stringify(curveDraft) !== JSON.stringify(originalPoints);

  const applyCurve = () => {
    if (readOnly || !validCurve || curveOverlap || !curveChanged) return;
    void setClipCurve(dispatch, getLatestState() || state, {
      clipId: clip.id, points: curveDraft,
      frameInterpolation: curveDraft.some((point) => point.speed < 1) ? frameInterpolation : "none",
    });
  };

  const movePoint = (event: PointerEvent<SVGSVGElement>) => {
    const index = dragIndex.current;
    if (index == null || readOnly) return;
    const box = event.currentTarget.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, ((event.clientX - box.left) / box.width * 300 - 20) / 260));
    const y = Math.max(0, Math.min(1, ((event.clientY - box.top) / box.height * 140 - 20) / 100));
    setCurveDraft((current) => current.map((point, atIndex) => atIndex === index
      ? { at: index === 0 || index === current.length - 1 ? point.at
          : Math.max(current[index - 1].at + 0.02,
                     Math.min(current[index + 1].at - 0.02, Math.round(x * 1000) / 1000)),
          speed: Math.round((8 - y * 7.9) * 100) / 100 }
      : point));
  };

  const apply = (speed = targetSpeed) => {
    if (readOnly || !Number.isFinite(speed) || Math.abs(speed) < MIN_SPEED || Math.abs(speed) > MAX_SPEED) return;
    if (timelineStart + sourceDuration / Math.abs(speed) > nextStart + 1e-6) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "新时长会与同轨后续片段重叠，请先移开后续片段" });
      return;
    }
    const st = getLatestState() || state;
    void setClipSpeed(dispatch, st, { clipId: clip.id, speed: secsToRational(speed),
      preservePitch: draftPreservePitch, frameInterpolation:
        Math.abs(speed) < 1 ? frameInterpolation : "none" });
  };

  return (
    <Panel title="变速" subtitle="恒定倍速、倒放与速度曲线">
      <div className="cv-row">
        <Gauge size={14} className="cv-ic--video" />
        <span className="cv-field__label">{storedCurve ? "当前平均倍速" : "当前倍速"}</span>
        <span className="cv-mono" style={{ marginLeft: "auto" }}>{currentSpeed.toFixed(2)}x</span>
      </div>
      {readOnly ? (
        <p className="cv-hint" role="status">
          {state.editLock ? "Agent 正在编辑，变速暂不可用。" : "所选片段所在轨道已锁定，解锁后可调整速度。"}
        </p>
      ) : null}
      <div className="cv-row" role="tablist" aria-label="变速模式" style={{ marginTop: 10 }}>
        <button role="tab" aria-selected={mode === "constant"}
          className={`cv-chip ${mode === "constant" ? "cv-chip--on" : ""}`}
          onClick={() => setMode("constant")}>常规变速</button>
        <button role="tab" aria-selected={mode === "curve"}
          className={`cv-chip ${mode === "curve" ? "cv-chip--on" : ""}`}
          onClick={() => setMode("curve")}>曲线变速</button>
      </div>
      <div className="cv-row" style={{ marginTop: 10, gap: 8 }}>
        <label className="cv-field__label" htmlFor="speed-frame-interpolation">慢动作补帧</label>
        <select id="speed-frame-interpolation" className="cv-select"
          aria-label="慢动作补帧模式" value={frameInterpolation}
          disabled={readOnly || !hasSlowMotion}
          onChange={(event) => void setClipFrameInterpolation(
            dispatch, getLatestState() || state,
            { clipId: clip.id, frameInterpolation: event.target.value as "none" | "motion" },
          )}>
          <option value="none">关闭</option>
          <option value="motion">运动估算</option>
        </select>
      </div>
      <p className="cv-hint" style={{ marginTop: 5 }} role="status">
        {hasSlowMotion
          ? "运动估算会合成中间帧，导出较慢，遮挡或快速运动可能变形；需要 FFmpeg minterpolate。"
          : "把片段设为低于 1× 后可启用；速度曲线只要包含慢速区间即可。"}
      </p>
      {mode === "constant" ? <div role="tabpanel" aria-label="常规变速">
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 8 }}>
        {PRESETS.map((p) => (
          <button
            key={p}
            className={`cv-chip ${Math.abs(currentSpeed - p) < 1e-6 ? "cv-chip--on" : ""}`}
            disabled={readOnly}
            onClick={() => {
              setDraftMagnitude(String(p));
              setDraftReversed(false);
              apply(p);
            }}
          >
            {p}x
          </button>
        ))}
        <button
          className={`cv-chip ${draftReversed ? "cv-chip--on" : ""}`}
          disabled={readOnly}
          aria-pressed={draftReversed}
          onClick={() => setDraftReversed((value) => !value)}
          title="在应用前切换播放方向"
        >
          <RotateCcw size={11} /> 倒放
        </button>
      </div>
      <div className="cv-row" style={{ marginTop: 12, gap: 8 }}>
        <label className="cv-field__label" htmlFor="speed-exact-value">精确倍速</label>
        <input
          id="speed-exact-value"
          type="number"
          min={MIN_SPEED}
          max={MAX_SPEED}
          step="0.01"
          value={draftMagnitude}
          disabled={readOnly}
          onChange={(event) => setDraftMagnitude(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && validSpeed && !wouldOverlap && changed) apply();
          }}
          aria-describedby="speed-preview-hint"
          style={{ width: 82 }}
        />
        <span className="cv-field__label">x</span>
      </div>
      <input
        type="range"
        min="0.25"
        max="4"
        step="0.05"
        value={validSpeed ? Math.max(0.25, Math.min(4, draftValue)) : 1}
        disabled={readOnly}
        onChange={(event) => setDraftMagnitude(event.target.value)}
        aria-label="倍速滑杆"
        style={{ width: "100%", marginTop: 8 }}
      />
      <label className="cv-row" style={{ marginTop: 10, gap: 8 }}>
        <input
          type="checkbox"
          checked={draftPreservePitch}
          disabled={readOnly}
          onChange={(event) => setDraftPreservePitch(event.target.checked)}
        />
        <span className="cv-field__label">音频保持音高</span>
      </label>
      <div style={{ marginTop: 8 }} className="inspector__row">
        <span className="inspector__key">源范围时长</span>
        <span className="inspector__val">{sourceDuration.toFixed(3)}s</span>
      </div>
      <div style={{ marginTop: 8 }} className="inspector__row">
        <span className="inspector__key">当前时间线时长</span>
        <span className="inspector__val">{durSecs.toFixed(3)}s</span>
      </div>
      <div style={{ marginTop: 8 }} className="inspector__row">
        <span className="inspector__key">应用后时长</span>
        <span className="inspector__val">{validSpeed ? effDur.toFixed(3) : "—"}s</span>
      </div>
      <div style={{ marginTop: 8 }} className="inspector__row">
        <span className="inspector__key">应用后结束</span>
        <span className="inspector__val">{validSpeed ? fmtTimePrecise(proposedEnd) : "—"}</span>
      </div>
      <p id="speed-preview-hint" className="cv-hint" role="status" style={{ marginTop: 8 }}>
        {!validSpeed
          ? `输入 ${MIN_SPEED}–${MAX_SPEED}x 的倍速。`
          : wouldOverlap
            ? `结束位置超过同轨下一片段起点 ${fmtTimePrecise(nextStart)}；请先移开后续片段。`
            : `时长变化 ${effDur - durSecs >= 0 ? "+" : ""}${(effDur - durSecs).toFixed(3)}s；其他片段与独立音轨不会自动移动。`}
      </p>
      <button className="cv-chip" disabled={readOnly || !validSpeed || wouldOverlap || !changed} onClick={() => apply()}>应用变速设置</button>
      <p className="cv-hint" style={{ marginTop: 8 }}>音频随画面变速；关闭保调后音高也随倍速变化。倍速与保调一次应用、一次撤销。</p>
      </div> : <div role="tabpanel" aria-label="曲线变速" className="cv-speed-curve">
        <p className="cv-hint">横轴为源素材位置，纵轴为播放倍率。拖动节点或用下方数字精调；音频保持原音高。</p>
        <div className="cv-row" style={{ flexWrap: "wrap", gap: 4 }}>
          {CURVE_PRESETS.map((preset) => (
            <button key={preset.label} className="cv-chip" disabled={readOnly}
              onClick={() => setCurveDraft(defaultCurve(1).map((point, index) =>
                ({ ...point, speed: preset.speeds[index] })))}>{preset.label}</button>
          ))}
        </div>
        <svg viewBox="0 0 300 140" className="cv-speed-curve__plot"
          aria-label="速度曲线，节点可拖动" role="img"
          onPointerMove={movePoint}
          onPointerUp={(event) => {
            dragIndex.current = null;
            if (event.currentTarget.hasPointerCapture(event.pointerId))
              event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onPointerCancel={() => { dragIndex.current = null; }}>
          <line x1="20" y1="120" x2="280" y2="120" stroke="currentColor" opacity="0.5" />
          <line x1="20" y1="20" x2="20" y2="120" stroke="currentColor" opacity="0.5" />
          <polyline fill="none" stroke="var(--cv-accent, #60a5fa)" strokeWidth="2"
            points={curveDraft.map((point) =>
              `${20 + point.at * 260},${120 - (point.speed - 0.1) / 7.9 * 100}`).join(" ")} />
          {curveDraft.map((point, index) => (
            <circle key={index} cx={20 + point.at * 260}
              cy={120 - (point.speed - 0.1) / 7.9 * 100} r="6"
              fill="var(--cv-accent, #60a5fa)" stroke="white" strokeWidth="1.5"
              style={{ cursor: readOnly ? "default" : "grab" }}
              onPointerDown={(event) => {
                if (readOnly) return;
                dragIndex.current = index;
                event.currentTarget.ownerSVGElement?.setPointerCapture(event.pointerId);
              }} />
          ))}
        </svg>
        <div className="cv-speed-curve__points">
          {curveDraft.map((point, index) => (
            <div className="cv-speed-curve__point" key={index}>
              <span className="cv-field__label">节点 {index + 1}</span>
              <input type="number" min={index === 0 ? 0 : Math.ceil(curveDraft[index - 1].at * 100) + 1}
                max={index === curveDraft.length - 1 ? 100 : Math.floor(curveDraft[index + 1].at * 100) - 1}
                step="0.01" value={Math.round(point.at * 100)}
                aria-label={`节点 ${index + 1} 源位置百分比`}
                disabled={readOnly || index === 0 || index === curveDraft.length - 1}
                onChange={(event) => {
                  const at = Number(event.target.value) / 100;
                  setCurveDraft((current) => current.map((item, atIndex) =>
                    atIndex === index ? { ...item, at } : item));
                }} />
              <span className="cv-hint">%</span>
              <input type="number" min="0.1" max="8" step="0.05" value={point.speed}
                aria-label={`节点 ${index + 1} 倍率`} disabled={readOnly}
                onChange={(event) => {
                  const speed = Number(event.target.value);
                  setCurveDraft((current) => current.map((item, atIndex) =>
                    atIndex === index ? { ...item, speed } : item));
                }} />
              <span className="cv-hint">x</span>
              {index > 0 && index < curveDraft.length - 1 ? (
                <button className="cv-btn cv-btn--ghost cv-btn--sm" disabled={readOnly}
                  aria-label={`删除节点 ${index + 1}`}
                  onClick={() => setCurveDraft((current) => current.filter((_, atIndex) =>
                    atIndex !== index))}>删除</button>
              ) : null}
            </div>
          ))}
        </div>
        <button className="cv-btn cv-btn--secondary cv-btn--sm" disabled={readOnly || curveDraft.length >= 12}
          onClick={() => {
            let gapIndex = 0;
            for (let index = 1; index < curveDraft.length - 1; index++)
              if (curveDraft[index + 1].at - curveDraft[index].at >
                  curveDraft[gapIndex + 1].at - curveDraft[gapIndex].at) gapIndex = index;
            const left = curveDraft[gapIndex], right = curveDraft[gapIndex + 1];
            const next = [...curveDraft];
            next.splice(gapIndex + 1, 0, {
              at: Math.round((left.at + right.at) * 500) / 1000,
              speed: Math.round((left.speed + right.speed) * 50) / 100,
            });
            setCurveDraft(next);
          }}>添加节点</button>
        <div className="inspector__row"><span className="inspector__key">源范围时长</span>
          <span className="inspector__val">{sourceDuration.toFixed(3)}s</span></div>
        <div className="inspector__row"><span className="inspector__key">应用后时长</span>
          <span className="inspector__val">{validCurve ? curveDuration.toFixed(3) : "—"}s</span></div>
        <div className="inspector__row"><span className="inspector__key">应用后结束</span>
          <span className="inspector__val">{validCurve ? fmtTimePrecise(curveEnd) : "—"}</span></div>
        <p className="cv-hint" role="status">
          {!validCurve ? "节点位置需递增、首末为 0%/100%，倍率需在 0.1–8x。"
            : curveOverlap ? `结束位置超过同轨下一片段起点 ${fmtTimePrecise(nextStart)}；请先移开后续片段。`
              : `时长变化 ${curveDuration - durSecs >= 0 ? "+" : ""}${(curveDuration - durSecs).toFixed(3)}s；其它片段和独立音轨保持原位。`}
        </p>
        <button className="cv-btn cv-btn--primary" disabled={readOnly || !validCurve || curveOverlap || !curveChanged}
          onClick={applyCurve}>应用速度曲线</button>
      </div>}
    </Panel>
  );
}
