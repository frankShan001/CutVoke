/** 关键帧小节（J01 拆分自 Inspector.tsx）：聚焦 opacity 关键帧的增删。 */
import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { addKeyframe, removeKeyframe } from "../../store/clipEdit";
import { rationalToSecs } from "../../lib/rational";
import type { Clip, Keyframe, KeyframeInterpolation } from "../../types/api";

export function KeyframeSection({ clip }: { clip: Clip }) {
  const { state, dispatch } = useEditor();
  const [kfTime, setKfTime] = useState("0");
  const [kfValue, setKfValue] = useState("1");
  const [kfInterp, setKfInterp] = useState<KeyframeInterpolation>("linear");

  return (
    <>
      <div className="inspector__sep" />
      <div className="inspector__subrow">
        <span className="inspector__key">关键帧（opacity）</span>
      </div>
      <KeyframeList
        keyframes={clip.keyframes?.opacity ?? []}
        onRemove={(kid) => {
          const ctx = getLatestState() || state;
          void removeKeyframe(dispatch, ctx, {
            clipId: clip.id,
            param: "opacity",
            keyframeId: kid,
          });
        }}
      />
      <div className="inspector__subrow" style={{ marginTop: 6, gap: 4, flexWrap: "wrap" }}>
        <input
          type="number"
          step="0.1"
          min="0"
          value={kfTime}
          aria-label="关键帧时间（秒）"
          className="cv-input"
          style={{ width: 64, height: 26 }}
          onChange={(e) => setKfTime(e.target.value)}
          placeholder="秒"
        />
        <input
          type="number"
          step="0.05"
          min="0"
          max="1"
          value={kfValue}
          aria-label="关键帧 opacity 值（0-1）"
          className="cv-input"
          style={{ width: 64, height: 26 }}
          onChange={(e) => setKfValue(e.target.value)}
          placeholder="0-1"
        />
        <select
          value={kfInterp}
          aria-label="关键帧插值"
          className="cv-input"
          style={{ height: 26 }}
          onChange={(e) => setKfInterp(e.target.value as KeyframeInterpolation)}
        >
          <option value="linear">linear</option>
          <option value="ease-in">ease-in</option>
          <option value="ease-out">ease-out</option>
        </select>
        <button
          className="cv-btn cv-btn--sm cv-btn--secondary"
          onClick={() => {
            const t = parseFloat(kfTime);
            const v = parseFloat(kfValue);
            if (isNaN(t) || isNaN(v)) return;
            const ctx = getLatestState() || state;
            void addKeyframe(dispatch, ctx, {
              clipId: clip.id,
              param: "opacity",
              time: t,
              value: v,
              interpolation: kfInterp,
            });
          }}
        >
          <Plus size={10} /> 添加
        </button>
      </div>
      <p className="cv-hint" style={{ marginTop: 4 }}>
        在指定时间点设置 opacity（0=透明，1=不透明）；多个关键帧之间按插值平滑过渡。
      </p>
    </>
  );
}

function KeyframeList({
  keyframes,
  onRemove,
}: {
  keyframes: Keyframe[];
  onRemove: (keyframeId: string) => void;
}) {
  if (keyframes.length === 0) {
    return <p className="cv-hint">暂无 opacity 关键帧。</p>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
      {keyframes.map((kf) => (
        <div key={kf.id} className="inspector__subrow" style={{ gap: 6 }}>
          <span className="inspector__key">t={rationalToSecs(kf.time).toFixed(2)}s</span>
          <span className="inspector__val">值 {Number(kf.value).toFixed(2)}</span>
          <span className="cv-mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
            {kf.interpolation}
          </span>
          <button
            className="cv-btn cv-btn--sm cv-btn--danger"
            onClick={() => onRemove(kf.id)}
            title="删除关键帧"
            style={{ marginLeft: "auto" }}
          >
            <Trash2 size={11} /> 删除
          </button>
        </div>
      ))}
    </div>
  );
}
