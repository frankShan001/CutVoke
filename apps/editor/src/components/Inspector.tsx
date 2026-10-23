import { tr, useLocale } from "../i18n";
/** 检查器：展示当前选中对象（轨道或片段）的详细属性。
 *  主区只留人能懂的（素材文件名/时长/轨道类型/速度/效果）；
 *  ID、内部轨道 id、源路径、rational 区间、Revision/Schema 全部收进默认收起的「高级信息」。
 *  J01：动画 / 画面特效 / 音频 / 关键帧小节拆到 ./inspector/*；动画与特效清单从效果注册表
 *  派生（effects.ts），不再硬编码效果 ID 数组。 */

import { useEffect, useState } from "react";
import { useEditor, selectors } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { removeClip } from "../store/clipEdit";
import { stageEffect, useInteractionProject } from "../store/interactive";
import { rationalText, rationalToSecs } from "../lib/rational";
import { sourcePathShort } from "../lib/media";
import { Badge } from "./ui";
import { AudioSection } from "./inspector/AudioSection";
import { KeyframeSection } from "./inspector/KeyframeSection";
import { AnimationSection } from "./inspector/AnimationSection";
import { FxSection } from "./inspector/FxSection";
import { TitleEditor } from "./inspector/TitleEditor";
import { TransformSection } from "./inspector/TransformSection";
import { assetDisplayName, inferKind, useSessionAssets } from "../lib/assetStore";
import { BUILTIN_TRANSFORM_ID } from "../lib/effects";
import { ClipTimingSection } from "./inspector/ClipTimingSection";
import { clipSourceRange } from "../lib/clipTiming";
import { BatchClipSection } from "./inspector/BatchClipSection";
import type { Clip } from "../types/api";

export function Inspector() {
  useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  useSessionAssets();
  const { selection } = state;
  const project = useInteractionProject(state.project);
  if (!project) {
    return (
      <section className="inspector">
        <h3 className="inspector__title" style={{ margin: 0 }}>{tr("属性")}</h3>
        <p className="cv-empty">{tr("选择工程后可查看属性。")}</p>
      </section>
    );
  }

  if (state.selectedClipIds.length > 1) return <BatchClipSection />;

  // ---- 选中片段 ----
  if (selection?.clipId) {
    const track = selectors.trackById(project, selection.trackId);
    const clip = track?.clips.find((c) => c.id === selection.clipId);
    if (clip) {
      const clipReadOnly = !!track?.locked || !!state.editLock;
      const tlStart = rationalToSecs(clip.timelineStart);
      const tlEnd = rationalToSecs(clip.timelineEnd);
      const dur = tlEnd - tlStart;
      const sourceRange = clipSourceRange(clip);
      const srcEnd = sourceRange.end;
      const speed = clip.speed ? rationalText(clip.speed) : "1/1";
      const isSticker = clip.role === "sticker" || track?.role === "sticker";
      const isText = track?.kind === "text";
      const isAudio = track?.kind === "audio";
      const isImage = track?.kind === "video" && inferKind(clip.assetRef.sourcePath, false, false) === "image";
      const isVisualClip = track?.kind === "video" && !isSticker;
      const anchorChoices = project.sequence.tracks
        .filter((item) => item.kind === "video" && item.role !== "sticker" && item.id !== track?.id)
        .flatMap((item) => item.clips);
      const attachedCount = project.sequence.tracks.flatMap((item) => item.clips)
        .filter((item) => item.attachedToClipId === clip.id).length;
      const detachedAudioClipId = project.sequence.tracks
        .filter((item) => item.kind === "audio")
        .flatMap((item) => item.clips)
        .find((item) => item.attachedToClipId === clip.id &&
          item.assetRef.sourcePath === clip.assetRef.sourcePath)?.id;

      return (
        <section className="inspector">
          <h3 className="inspector__title" style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>{tr("片段")}<Badge tone={track!.kind === "audio" ? "audio" : "video"}>
              {isSticker ? tr("贴纸") : isText ? tr("文字") : track!.kind === "audio" ? tr("音频") : tr("视频")}
            </Badge>
          </h3>
          {!isText ? <Row k={tr("素材")} v={assetDisplayName(clip.assetRef)} /> : null}
          <Row k={tr("时长")} v={tr("{0} 秒", dur.toFixed(2))} />
          {attachedCount ? <Row k={tr("跟随片段")} v={tr("{0} 个", attachedCount)} /> : null}
          {!isSticker && !isText ? <Row k={tr("速度")} v={speed === "1/1" ? tr("正常（1x）") : tr("{0}（变速）", speed)} /> : null}

          {clipReadOnly ? (
            <p className="cv-hint" role="status">
              {state.editLock ? tr("Agent 正在编辑，片段属性暂时只读。") : tr("此轨道已锁定，解锁后才能修改片段属性。")}
            </p>
          ) : null}

          <fieldset
            disabled={clipReadOnly}
            inert={clipReadOnly}
            aria-label={tr("片段编辑属性")}
            style={{ border: 0, margin: 0, padding: 0, minWidth: 0, opacity: clipReadOnly ? 0.55 : 1, pointerEvents: clipReadOnly ? "none" : undefined }}
          >
            <ClipTimingSection clip={clip} hasSource={!isText && !clip.nested && !!clip.assetRef.sourcePath} />
            {(isText || isSticker || track?.kind === "audio") && anchorChoices.length ? <label className="inspector__subrow">
              <span className="inspector__key">{tr("跟随视频")}</span>
              <select className="cv-input" aria-label={tr("跟随视频片段")}
                value={clip.attachedToClipId || ""}
                onChange={(event) => {
                  const ctx = getLatestState() || state;
                  const attachedToClipId = event.target.value || null;
                  void runCommand(dispatch, ctx, "clip.attach", { clipId: clip.id, attachedToClipId });
                }}>
                <option value="">{tr("独立移动")}</option>
                {anchorChoices.map((candidate) => <option key={candidate.id} value={candidate.id}>
                  {assetDisplayName(candidate.assetRef) || tr("视频片段")} · {rationalToSecs(candidate.timelineStart).toFixed(1)}{tr(" 秒")}</option>)}
              </select>
            </label> : null}
            {clip.attachedToClipId ? <p className="cv-hint">{tr("移动关联视频时此片段同步移动；按住 Alt 拖动视频可临时保持此片段原位。")}</p> : null}
            {/* ---- 音频 / 关键帧 / 动画 / 画面特效 小节（J01 拆分）---- */}
            <div className="inspector__sep" />
            {isText ? <><TitleEditor key={clip.id} clip={clip} /><KeyframeSection clip={clip} /></> : <>
              {isSticker ? <StickerTransformSection clip={clip} /> : isVisualClip ? <>
                {isImage ? null : <AudioSection clip={clip} canDetach
                  detachedAudioClipId={detachedAudioClipId} />}
                <TransformSection clip={clip} />
              </> : isAudio ? <AudioSection clip={clip} /> : null}
              <KeyframeSection clip={clip} />
              {!isAudio ? <AnimationSection clip={clip} /> : null}
              <FxSection clip={clip} trackKind={track!.kind} />
            </>}


            <div className="inspector__subrow">
              <span className="inspector__key">{tr("删除")}</span>
              <button
                className="cv-btn cv-btn--sm cv-btn--danger"
                onClick={() => {
                  const ctx = getLatestState() || state;
                  void removeClip(dispatch, ctx, clip.id);
                }}
              >{tr("删除片段")}</button>
            </div>
          </fieldset>

          <details className="inspector__adv">
            <summary>{tr("高级信息")}</summary>
            <Row k={tr("片段 ID")} v={clip.id} mono />
            <Row k={tr("轨道 ID")} v={track!.id} mono />
            {!isText ? <Row k={tr("源路径")} v={sourcePathShort(clip.assetRef.sourcePath)} mono /> : null}
            <Row k={tr("时间线")} v={`[${rationalText(clip.timelineStart)} → ${rationalText(clip.timelineEnd)})`} mono />
            {!isText ? <Row
              k={tr("源区间")}
              v={`[${rationalText({ num: String(Math.round(sourceRange.start * 1000000)), den: "1000000" })} → ${rationalText({ num: String(Math.round(srcEnd * 1000000)), den: "1000000" })})`}
              mono
            /> : null}
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
          <h3 className="inspector__title" style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>{tr("轨道")}<Badge tone={track.kind === "audio" ? "audio" : "video"}>
              {track.role === "sticker" ? tr("贴纸") : track.kind === "text" ? tr("文字") : track.kind === "audio" ? tr("音频") : tr("视频")}
            </Badge>
          </h3>
          <Row k={tr("片段数")} v={String(track.clips.length)} />
          <Row k={tr("锁定")} v={track.locked ? tr("是") : tr("否")} />
          <Row k={tr("静音")} v={track.muted ? tr("是") : tr("否")} />
          <Row k={tr("可见")} v={track.visible ? tr("是") : tr("否")} />
          <details className="inspector__adv">
            <summary>{tr("高级信息")}</summary>
            <Row k={tr("轨道 ID")} v={track.id} mono />
          </details>
        </section>
      );
    }
  }

  // ---- 工程摘要 ----
  const seq = project.sequence;
  return (
    <section className="inspector">
      <h3 className="inspector__title" style={{ margin: 0 }}>{tr("工程属性")}</h3>
      <Row k={tr("画面")} v={`${seq.width}×${seq.height}`} />
      <Row k={tr("帧率")} v={rationalText(seq.fps)} />
      <Row k={tr("轨道数")} v={String(seq.tracks.length)} />
      <Row k={tr("片段数")} v={String(seq.tracks.reduce((n, t) => n + (t.clips?.length || 0), 0))} />
      <details className="inspector__adv">
        <summary>{tr("高级信息")}</summary>
        <Row k={tr("工程 ID")} v={project.projectId} mono />
        <Row k="Revision" v={project.revision} mono />
        <Row k="Schema" v={project.schemaVersion} mono />
      </details>
      <p className="cv-hint" style={{ marginTop: 8 }}>{tr("点击时间线中的轨道或片段查看详细属性；右键片段可分割/删除/调速/加效果。")}</p>
    </section>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  useLocale();
  return (
    <div className="inspector__row">
      <span className="inspector__key">{k}</span>
      <span className="inspector__val" style={mono ? undefined : { fontFamily: "var(--font-sans)" }}>
        {v}
      </span>
    </div>
  );
}

function StickerTransformSection({ clip }: { clip: Clip }) {
  useLocale();
  const { dispatch } = useEditor({ subscribeToClock: false });
  const existingTransform = clip.effects?.find((effect) => effect.effectId === BUILTIN_TRANSFORM_ID);
  const params = existingTransform?.params;
  const position = params?.position as { x?: number; y?: number } | undefined;
  const saved = [position?.x ?? 0, position?.y ?? 0, params?.scale ?? 0.28,
    params?.rotation ?? 0, params?.opacity ?? 1].map(String);
  const savedKey = saved.join("|");
  const [values, setValues] = useState(saved);

  useEffect(() => setValues(savedKey.split("|")), [clip.id, savedKey]);

  const fields = [
    { label: tr("水平位置 X（像素）"), step: "1" },
    { label: tr("垂直位置 Y（像素）"), step: "1" },
    { label: tr("缩放倍率"), step: "0.01" },
    { label: tr("旋转角度"), step: "1" },
    { label: tr("不透明度"), step: "0.01" },
  ];
  const numbers = values.map(Number);
  const valid = numbers.every(Number.isFinite) &&
    Number.isInteger(numbers[0]) && Number.isInteger(numbers[1]) &&
    numbers[2] > 0 && numbers[4] >= 0 && numbers[4] <= 1;
  const validationMessage = valid
    ? ""
    : tr("位置需为整数，缩放需大于 0，不透明度需在 0–1 范围内。");
  const validationMessageId = validationMessage ? `sticker-transform-validation-${clip.id}` : undefined;

  return (
    <section aria-label={tr("贴纸变换")} style={{ display: "grid", gap: 6 }}>
      <strong>{tr("贴纸变换")}</strong>
      {validationMessage ? <p id={validationMessageId} className="cv-hint" role="status">
        {validationMessage}
      </p> : null}
      {fields.map((field, index) => (
        <label key={field.label} className="inspector__subrow">
          <span className="inspector__key">{field.label}</span>
          <input
            className="cv-input"
            type="number"
            step={field.step}
            value={values[index]}
            onChange={(event) => {
              const next = values.map((value, i) => i === index ? event.target.value : value);
              setValues(next);
              const n = next.map(Number);
              if (next.every(v => v.trim() !== "") && n.every(Number.isFinite) && Number.isInteger(n[0]) && Number.isInteger(n[1]) && n[2] > 0 && n[2] <= 5 && Math.abs(n[3]) <= 180 && n[4] >= 0 && n[4] <= 1)
                stageEffect(dispatch, clip, BUILTIN_TRANSFORM_ID, { position: { x: n[0], y: n[1] }, scale: n[2], rotation: n[3], opacity: n[4] });
            }}
            style={{ width: 86, height: 26 }}
          />
        </label>
      ))}
    </section>
  );
}
