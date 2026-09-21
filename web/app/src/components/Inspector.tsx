/** 检查器：展示当前选中对象（轨道或片段）的详细属性。
 *  主区只留人能懂的（素材文件名/时长/轨道类型/速度/效果）；
 *  ID、内部轨道 id、源路径、rational 区间、Revision/Schema 全部收进默认收起的「高级信息」。
 *  J01：动画 / 画面特效 / 音频 / 关键帧小节拆到 ./inspector/*；动画与特效清单从效果注册表
 *  派生（effects.ts），不再硬编码效果 ID 数组。 */

import { useEffect, useState } from "react";
import { useEditor, selectors } from "../store/editor";
import { getLatestState } from "../store/actions";
import { removeClip, setClipSpeed } from "../store/clipEdit";
import { rationalText, rationalToSecs, secsToRational } from "../lib/rational";
import { sourceBasename, sourcePathShort } from "../lib/media";
import { Badge } from "./ui";
import { AudioSection } from "./inspector/AudioSection";
import { KeyframeSection } from "./inspector/KeyframeSection";
import { AnimationSection } from "./inspector/AnimationSection";
import { FxSection } from "./inspector/FxSection";
import { EffChip } from "./inspector/parts";
import { BUILTIN_TRANSFORM_ID, BUILTIN_COLOR_ID, BUILTIN_CROSSFADE_ID } from "../lib/effects";

export function Inspector() {
  const { state, dispatch } = useEditor();
  const { project, selection } = state;
  const [speedInput, setSpeedInput] = useState("1");

  // 切换片段时复位调速输入
  useEffect(() => {
    setSpeedInput("1");
  }, [selection?.clipId]);

  if (!project) {
    return (
      <section className="inspector">
        <h3 className="inspector__title" style={{ margin: 0 }}>属性</h3>
        <p className="cv-empty">选择工程后可查看属性。</p>
      </section>
    );
  }

  // ---- 选中片段 ----
  if (selection?.clipId) {
    const track = selectors.trackById(project, selection.trackId);
    const clip = track?.clips.find((c) => c.id === selection.clipId);
    if (clip) {
      const tlStart = rationalToSecs(clip.timelineStart);
      const tlEnd = rationalToSecs(clip.timelineEnd);
      const srcStart = rationalToSecs(clip.sourceStart);
      const dur = tlEnd - tlStart;
      const srcEnd = srcStart + dur; // 近似（未考虑 speed，仅展示）
      const speed = clip.speed ? rationalText(clip.speed) : "1/1";
      const fxCount = clip.effects?.length || 0;

      const applySpeed = () => {
        const v = parseFloat(speedInput);
        if (isNaN(v) || !v) return;
        const ctx = getLatestState() || state;
        void setClipSpeed(dispatch, ctx, { clipId: clip.id, speed: secsToRational(v) });
      };

      return (
        <section className="inspector">
          <h3 className="inspector__title" style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
            片段
            <Badge tone={track!.kind === "audio" ? "audio" : "video"}>
              {track!.kind === "audio" ? "音频" : "视频"}
            </Badge>
          </h3>
          <Row k="素材" v={sourceBasename(clip.assetRef.sourcePath)} />
          <Row k="时长" v={`${dur.toFixed(2)} 秒`} />
          <Row k="速度" v={speed === "1/1" ? "正常（1x）" : `${speed}（变速）`} />

          <div className="inspector__subrow">
            <span className="inspector__key">调速</span>
            <input
              type="number"
              step="0.1"
              value={speedInput}
              onChange={(e) => setSpeedInput(e.target.value)}
              className="cv-input"
              style={{ width: 80, height: 26 }}
              aria-label="调速倍率"
            />
            <button className="cv-btn cv-btn--sm cv-btn--secondary" onClick={applySpeed}>
              应用
            </button>
          </div>

          {/* ---- 音频 / 关键帧 / 动画 / 画面特效 小节（J01 拆分）---- */}
          <div className="inspector__sep" />
          <AudioSection clip={clip} />
          <KeyframeSection clip={clip} />
          <AnimationSection clip={clip} />
          <FxSection clip={clip} />

          <div className="inspector__sep" />
          <div className="inspector__subrow">
            <span className="inspector__key">效果 ({fxCount})</span>
            <div style={{ display: "flex", gap: 4 }}>
              <EffChip label="变换" effectId={BUILTIN_TRANSFORM_ID} clipId={clip.id} />
              <EffChip label="调色" effectId={BUILTIN_COLOR_ID} clipId={clip.id} />
              <EffChip label="叠化" effectId={BUILTIN_CROSSFADE_ID} clipId={clip.id} />
            </div>
          </div>
          <div className="inspector__subrow">
            <span className="inspector__key">删除</span>
            <button
              className="cv-btn cv-btn--sm cv-btn--danger"
              onClick={() => {
                const ctx = getLatestState() || state;
                void removeClip(dispatch, ctx, clip.id);
              }}
            >
              删除片段
            </button>
          </div>

          <details className="inspector__adv">
            <summary>高级信息</summary>
            <Row k="片段 ID" v={clip.id} mono />
            <Row k="轨道 ID" v={track!.id} mono />
            <Row k="源路径" v={sourcePathShort(clip.assetRef.sourcePath)} mono />
            <Row k="时间线" v={`[${rationalText(clip.timelineStart)} → ${rationalText(clip.timelineEnd)})`} mono />
            <Row
              k="源区间"
              v={`[${rationalText(clip.sourceStart)} → ${rationalText({ num: String(Math.round(srcEnd * 1000)), den: "1000" })})`}
              mono
            />
          </details>
        </section>
      );
    }
  }

  // ---- 选中轨道 ----
  if (selection?.trackId) {
    const track = selectors.trackById(project, selection.trackId);
    if (track) {
      return (
        <section className="inspector">
          <h3 className="inspector__title" style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
            轨道
            <Badge tone={track.kind === "audio" ? "audio" : "video"}>
              {track.kind === "audio" ? "音频" : "视频"}
            </Badge>
          </h3>
          <Row k="片段数" v={String(track.clips.length)} />
          <Row k="锁定" v={track.locked ? "是" : "否"} />
          <Row k="静音" v={track.muted ? "是" : "否"} />
          <Row k="可见" v={track.visible ? "是" : "否"} />
          <details className="inspector__adv">
            <summary>高级信息</summary>
            <Row k="轨道 ID" v={track.id} mono />
          </details>
        </section>
      );
    }
  }

  // ---- 工程摘要 ----
  const seq = project.sequence;
  return (
    <section className="inspector">
      <h3 className="inspector__title" style={{ margin: 0 }}>工程属性</h3>
      <Row k="画面" v={`${seq.width}×${seq.height}`} />
      <Row k="帧率" v={rationalText(seq.fps)} />
      <Row k="轨道数" v={String(seq.tracks.length)} />
      <Row k="片段数" v={String(seq.tracks.reduce((n, t) => n + (t.clips?.length || 0), 0))} />
      <details className="inspector__adv">
        <summary>高级信息</summary>
        <Row k="工程 ID" v={project.projectId} mono />
        <Row k="Revision" v={project.revision} mono />
        <Row k="Schema" v={project.schemaVersion} mono />
      </details>
      <p className="cv-hint" style={{ marginTop: 8 }}>
        点击时间线中的轨道或片段查看详细属性；右键片段可分割/删除/调速/加效果。
      </p>
    </section>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="inspector__row">
      <span className="inspector__key">{k}</span>
      <span className="inspector__val" style={mono ? undefined : { fontFamily: "var(--font-sans)" }}>
        {v}
      </span>
    </div>
  );
}
