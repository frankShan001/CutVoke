import { tr, useLocale } from "../i18n";
/** 片段右键菜单：split / remove / speed / effect.add。受控弹层。 */

import { useEffect, useLayoutEffect, useRef, useState, type Dispatch } from "react";
import { Scissors, Trash2, Gauge, Wand2, Copy } from "lucide-react";
import type { Clip } from "../types/api";
import { getLatestState, runCommand } from "../store/actions";
import { addEffect, duplicateClipGroup, setClipSpeed, splitClip } from "../store/clipEdit";
import type { EditorAction } from "../store/editor";
import { rationalToSecs, secsToRational } from "../lib/rational";
import { assetDisplayName, useSessionAssets } from "../lib/assetStore";
import { BUILTIN_TRANSFORM_ID, BUILTIN_COLOR_ID, BUILTIN_CROSSFADE_ID } from "../lib/effects";
import { flushPendingEdits } from "../lib/pendingEdits";
import { batchClips, copySelectedClips, duplicateSelectedClips, pasteClipsAtPlayhead } from "../store/timelineClipboard";
import { selectedClipIds } from "../store/timelineSelection";

export interface CtxTarget {
  x: number;
  y: number;
  clipId: string;
  clip: Clip | null;
  trackId: string;
  /** 播放头时间（秒）：分割默认点。 */
  playheadSecs?: number;
}

export function ClipContextMenu({
  target,
  onClose,
  dispatch,
}: {
  target: CtxTarget;
  onClose: () => void;
  dispatch: Dispatch<EditorAction>;
}) {
  const locale = useLocale();
  const menu = useRef<HTMLDivElement>(null);
  // Keep this listener mounted while selection/polling updates rerender the row.
  // A preceding capture listener can dispatch during the same Escape event.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const [position, setPosition] = useState({ x: target.x, y: target.y });
  useLayoutEffect(() => {
    const rect = menu.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({ x: Math.max(8, Math.min(target.x, innerWidth - rect.width - 8)),
      y: Math.max(8, Math.min(target.y, innerHeight - rect.height - 8)) });
  }, [target.x, target.y, locale]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); closeRef.current(); } };
    window.addEventListener("keydown", keydown, true);
    return () => window.removeEventListener("keydown", keydown, true);
  }, []);
  useSessionAssets();
  const st = getLatestState();
  if (!st || !st.currentId) return null;
  const ctx = st;
  const clip = target.clip;
  const count = selectedClipIds(ctx).length;

  const run = (fn: () => Promise<unknown>) => {
    onClose();
    void (async () => { if (await flushPendingEdits()) await fn(); })();
  };

  const handleSplit = () => {
    if (!clip) return;
    let at: number;
    const playhead = target.playheadSecs;
    const cs = rationalToSecs(clip.timelineStart);
    const ce = rationalToSecs(clip.timelineEnd);
    if (playhead != null && playhead > cs + 0.01 && playhead < ce - 0.01) {
      at = playhead;
    } else {
      at = (cs + ce) / 2;
    }
    run(() => splitClip(dispatch, ctx, { clipId: clip.id, time: secsToRational(at) }));
  };

  const handleRemove = () => {
    if (!clip) return;
    run(() => batchClips(dispatch, getLatestState() || ctx, "remove"));
  };

  const handleDuplicate = () => {
    if (!clip) return;
    run(() => duplicateSelectedClips(dispatch, getLatestState() || ctx));
  };

  const handleSpeed = (speed: number) => {
    if (!clip) return;
    run(() => setClipSpeed(dispatch, ctx, { clipId: clip.id, speed }));
  };

  const handleEffect = (effectId: string) => {
    if (!clip) return;
    run(() =>
      addEffect(dispatch, ctx, {
        clipId: clip.id,
        effectId,
        params: defaultParamsFor(effectId),
      }),
    );
  };

  return (
    <>
      <div
        className="ctx-menu__backdrop"
        onClick={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
      />
      <div ref={menu} className="ctx-menu" dir={locale === "ar" ? "rtl" : "ltr"}
        style={{ left: position.x, top: position.y }}>
        <div className="ctx-menu__label">
          {clip ? assetDisplayName(clip.assetRef) : tr("片段")}
        </div>
        <button className="ctx-menu__item" disabled={count > 1} onClick={handleSplit}>
          <Scissors size={14} />{tr(" 在播放头分割")}</button>
        <button className="ctx-menu__item" onClick={handleDuplicate}>
          <Copy size={14} />{tr(" 复制片段")}</button>
        <button className="ctx-menu__item" onClick={()=>run(()=>copySelectedClips(dispatch,getLatestState() || ctx))}>
          <Copy size={14} />{tr("复制到剪贴板")}</button>
        <button className="ctx-menu__item" onClick={()=>run(()=>pasteClipsAtPlayhead(dispatch,getLatestState() || ctx))}>
          <Copy size={14} />{tr("粘贴到播放头")}</button>
        <button className="ctx-menu__item" onClick={() => clip && run(() => duplicateClipGroup(dispatch, ctx, clip.id))}>
          <Copy size={14} />{tr("复制片段及绑定图层")}</button>
        <button className="ctx-menu__item ctx-menu__item--danger" onClick={handleRemove}>
          <Trash2 size={14} />{tr(" 删除片段")}</button>
        <button className="ctx-menu__item ctx-menu__item--danger" disabled={!!ctx.editLock}
          onClick={() => clip && run(() => runCommand(dispatch, ctx, "clip.rippleDelete", { clipId: clip.id, scope: "sequence" }))}>
          <Trash2 size={14} />{tr("删除并全轨联动收拢")}</button>
        {clip?.nested ? <button className="ctx-menu__item" disabled={!!ctx.editLock}
          onClick={() => run(() => runCommand(dispatch, ctx, "clip.ungroup", { clipId: clip.id }))}>
          <Copy size={14} />{tr("展开复合片段")}</button> : null}
        <div className="ctx-menu__sep" />
        <div className="ctx-menu__label">{tr("速度")}</div>
        <button className="ctx-menu__item" onClick={() => handleSpeed(0.5)}><Gauge size={14} /> 0.5x</button>
        <button className="ctx-menu__item" onClick={() => handleSpeed(1)}><Gauge size={14} /> 1.0x</button>
        <button className="ctx-menu__item" onClick={() => handleSpeed(1.5)}><Gauge size={14} /> 1.5x</button>
        <button className="ctx-menu__item" onClick={() => handleSpeed(2)}><Gauge size={14} /> 2.0x</button>
        <button className="ctx-menu__item" onClick={() => handleSpeed(-1)}><Gauge size={14} />{tr(" 倒放 (-1x)")}</button>
        <div className="ctx-menu__sep" />
        <div className="ctx-menu__label">{tr("效果")}</div>
        <button className="ctx-menu__item" onClick={() => handleEffect(BUILTIN_TRANSFORM_ID)}>
          <Wand2 size={14} />{tr(" 画面变换")}</button>
        <button className="ctx-menu__item" onClick={() => handleEffect(BUILTIN_COLOR_ID)}>
          <Wand2 size={14} />{tr(" 基础调色")}</button>
        <button className="ctx-menu__item" onClick={() => handleEffect(BUILTIN_CROSSFADE_ID)}>
          <Wand2 size={14} />{tr(" 叠化转场")}</button>
      </div>
    </>
  );
}

function defaultParamsFor(effectId: string): Record<string, unknown> {
  switch (effectId) {
    case BUILTIN_TRANSFORM_ID:
      return { position: { x: 0, y: 0 }, scale: 1.0, rotation: 0, opacity: 1.0 };
    case BUILTIN_COLOR_ID:
      return { brightness: 0.0, contrast: 1.0, saturation: 1.0 };
    case BUILTIN_CROSSFADE_ID:
      return { duration: 1.0 };
    default:
      return {};
  }
}
