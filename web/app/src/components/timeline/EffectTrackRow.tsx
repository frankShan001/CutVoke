import { memo, useRef, useState } from "react";
import { Sparkles, Trash2 } from "lucide-react";
import type { EffectInstance, Track } from "../../types/api";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { removeEffect } from "../../store/clipEdit";
import { updateEffect } from "../../store/effectEdit";
import { secsToRational } from "../../lib/rational";
import { effectLabel, type EffectSpec } from "../../lib/effects";
import { isTransitionEffectId } from "../../lib/transitions";
import { clipEndSecs, clipStartSecs, toPx } from "./util";
import type { TimelineViewport } from "./useTimelineViewport";

interface EffectSegment {
  clipId: string;
  effect: EffectInstance;
  index: number;
  clipStart: number;
  clipDuration: number;
  localStart: number;
  localEnd: number;
  editableRange: boolean;
}

type RangeDrag = {
  key: string;
  pointerId: number;
  mode: "move" | "start" | "end";
  startX: number;
  originStart: number;
  originEnd: number;
  clipDuration: number;
};

const seconds = (value: { num: string; den: string }) => Number(value.num) / Number(value.den);
const segmentKey = (segment: EffectSegment) => `${segment.clipId}:${segment.index}`;

export const EffectTrackRow = memo(function EffectTrackRow({
  track,
  displayName,
  pxPerSec,
  effectSpecs,
  viewport,
}: {
  track: Track;
  displayName: string;
  pxPerSec: number;
  effectSpecs: EffectSpec[];
  viewport: TimelineViewport;
}) {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const locked = !!track.locked || !!state.editLock;
  const [rangeDraft, setRangeDraft] = useState<{ key: string; start: number; end: number } | null>(null);
  const rangeDrag = useRef<RangeDrag | null>(null);
  const fpsValue = state.project?.sequence.fps;
  const frameDuration = fpsValue && Number(fpsValue.num) > 0
    ? Number(fpsValue.den) / Number(fpsValue.num)
    : 1 / 30;
  const segments: EffectSegment[] = track.clips.flatMap((clip) =>
    (clip.effects || [])
      .map((effect, index) => {
        const clipStart = clipStartSecs(clip);
        const clipDuration = clipEndSecs(clip) - clipStart;
        const localStart = effect.range ? Math.max(0, Math.min(clipDuration, seconds(effect.range.start))) : 0;
        const localEnd = effect.range ? Math.max(0, Math.min(clipDuration, seconds(effect.range.end))) : clipDuration;
        return {
          clipId: clip.id,
          effect,
          index,
          clipStart,
          clipDuration,
          localStart,
          localEnd,
          editableRange: String(effect.effectId).startsWith("cutvoke.fx."),
        };
      })
      .filter((item) => !isTransitionEffectId(String(item.effect.effectId)) && item.localEnd > item.localStart),
  );

  const grouped = new Map<string, EffectSegment[]>();
  for (const segment of segments) {
    const list = grouped.get(segment.clipId) || [];
    list.push(segment);
    grouped.set(segment.clipId, list);
  }

  const selectEffect = (segment: EffectSegment) => {
    dispatch({
      type: "SELECTION_SET",
      selection: {
        trackId: track.id,
        clipId: segment.clipId,
        effectId: String(segment.effect.effectId),
        effectIndex: segment.index,
        effectKind: "effect",
      },
    });
    dispatch({ type: "PLAYHEAD_SET", t: segment.clipStart + segment.localStart });
  };

  const deleteEffect = async (segment: EffectSegment) => {
    if (locked) return;
    const result = await removeEffect(dispatch, getLatestState() || state, {
      clipId: segment.clipId,
      effectId: String(segment.effect.effectId),
    });
    if (result.ok) dispatch({ type: "SELECTION_SET", selection: null });
  };

  const calculateRange = (drag: RangeDrag, clientX: number) => {
    const delta = Math.round((clientX - drag.startX) / pxPerSec / frameDuration) * frameDuration;
    const duration = Math.max(frameDuration, drag.clipDuration);
    const minimum = Math.min(frameDuration, duration);
    if (drag.mode === "move") {
      const length = drag.originEnd - drag.originStart;
      const start = Math.max(0, Math.min(duration - length, drag.originStart + delta));
      return { start, end: start + length };
    }
    if (drag.mode === "start") {
      return { start: Math.max(0, Math.min(drag.originEnd - minimum, drag.originStart + delta)), end: drag.originEnd };
    }
    return { start: drag.originStart, end: Math.max(drag.originStart + minimum, Math.min(duration, drag.originEnd + delta)) };
  };

  const beginRangeDrag = (event: React.PointerEvent<HTMLDivElement>, segment: EffectSegment) => {
    if (!segment.editableRange || locked || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const target = event.target as HTMLElement;
    const edge = target.closest<HTMLElement>("[data-range-resize]")?.dataset.rangeResize;
    const mode = edge === "start" || edge === "end" ? edge : "move";
    const drag: RangeDrag = {
      key: segmentKey(segment),
      pointerId: event.pointerId,
      mode,
      startX: event.clientX,
      originStart: rangeDraft?.key === segmentKey(segment) ? rangeDraft.start : segment.localStart,
      originEnd: rangeDraft?.key === segmentKey(segment) ? rangeDraft.end : segment.localEnd,
      clipDuration: segment.clipDuration,
    };
    rangeDrag.current = drag;
    event.currentTarget.setPointerCapture(event.pointerId);
    setRangeDraft({ key: drag.key, start: drag.originStart, end: drag.originEnd });
    selectEffect(segment);
  };

  const moveRangeDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = rangeDrag.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const next = calculateRange(drag, event.clientX);
    setRangeDraft({ key: drag.key, ...next });
  };

  const finishRangeDrag = async (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = rangeDrag.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const next = calculateRange(drag, event.clientX);
    rangeDrag.current = null;
    setRangeDraft(null);
    if (Math.abs(next.start - drag.originStart) < 0.0005 && Math.abs(next.end - drag.originEnd) < 0.0005) return;
    const segment = segments.find((item) => segmentKey(item) === drag.key);
    if (!segment) return;
    await updateEffect(dispatch, getLatestState() || state, {
      clipId: segment.clipId,
      effectId: String(segment.effect.effectId),
      effectIndex: segment.index,
      range: { start: secsToRational(next.start.toFixed(6)), end: secsToRational(next.end.toFixed(6)) },
    });
  };

  return (
    <div className={`timeline-track timeline-track--effects${locked ? " timeline-track--locked" : ""}`}>
      <div className="timeline-track__label timeline-track__label--effects" title={`${displayName}（跟随上方视频轨）`}>
        <span className="timeline-track__kind timeline-track__kind--effect"><Sparkles size={11} /></span>
        <span className="timeline-track__name">{displayName}</span>
      </div>
      <div
        className="timeline-track__lane timeline-track__lane--effects"
        onClick={() => dispatch({ type: "SELECTION_SET", selection: { trackId: track.id } })}
      >
        {segments.length === 0 ? <span className="timeline-track__lane-empty">暂无效果</span> : null}
        {[...grouped.entries()].filter(([clipId, items]) => track.clips.length <= 100 || clipId === state.selection?.clipId || rangeDraft?.key.startsWith(`${clipId}:`) ||
          (items[0].clipStart + items[0].clipDuration >= viewport.start && items[0].clipStart <= viewport.end)).map(([clipId, items]) => {
          const clipStart = items[0].clipStart;
          const clipDuration = items[0].clipDuration;
          return (
            <div
              key={clipId}
              className="effect-segment-group"
              style={{ left: toPx(clipStart, pxPerSec), width: Math.max(12, toPx(clipDuration, pxPerSec)) }}
            >
              {items.map((segment) => {
                const id = String(segment.effect.effectId);
                const label = effectLabel(effectSpecs, id);
                const key = segmentKey(segment);
                const draft = rangeDraft?.key === key ? rangeDraft : null;
                const localStart = draft?.start ?? segment.localStart;
                const localEnd = draft?.end ?? segment.localEnd;
                const selected =
                  state.selection?.trackId === track.id &&
                  state.selection?.clipId === segment.clipId &&
                  state.selection?.effectId === id &&
                  (state.selection?.effectIndex === undefined || state.selection?.effectIndex === segment.index) &&
                  state.selection?.effectKind === "effect";
                const stackOffset = Math.min(3, segment.index) * 3;
                const absoluteStart = segment.clipStart + localStart;
                const absoluteEnd = segment.clipStart + localEnd;
                return (
                  <div
                    key={key}
                    data-testid="effect-segment"
                    data-effect-id={id}
                    data-effect-index={segment.index}
                    className={`effect-segment${selected ? " effect-segment--selected" : ""}${segment.editableRange ? " effect-segment--range-editable" : ""}`}
                    style={{
                      left: toPx(localStart, pxPerSec),
                      width: Math.max(18, toPx(localEnd - localStart, pxPerSec)),
                      top: stackOffset,
                      bottom: stackOffset,
                      zIndex: segment.index + 1,
                    }}
                    title={segment.editableRange
                      ? `${label} · 拖动调整区间，拖动两侧手柄缩放区间`
                      : `${label} · 单击选中，Delete 删除`}
                    role="group"
                    aria-label={`${label}，位于 ${absoluteStart.toFixed(2)} 到 ${absoluteEnd.toFixed(2)} 秒`}
                    onClick={(event) => {
                      event.stopPropagation();
                      selectEffect(segment);
                    }}
                    onPointerDown={(event) => beginRangeDrag(event, segment)}
                    onPointerMove={moveRangeDrag}
                    onPointerUp={(event) => void finishRangeDrag(event)}
                    onPointerCancel={(event) => void finishRangeDrag(event)}
                  >
                    {segment.editableRange ? <span className="effect-segment__resize effect-segment__resize--start" data-range-resize="start" aria-label={`调整${label}起点`} /> : null}
                    <button
                      className="effect-segment__select"
                      type="button"
                      aria-pressed={selected}
                      aria-label={`${label}，位于 ${absoluteStart.toFixed(2)} 到 ${absoluteEnd.toFixed(2)} 秒`}
                      onClick={(event) => {
                        event.stopPropagation();
                        selectEffect(segment);
                      }}
                    >
                      <span className="effect-segment__name">{label}</span>
                    </button>
                    <button
                      className="effect-segment__delete"
                      type="button"
                      disabled={locked}
                      aria-label={`删除效果 ${label}`}
                      title={locked ? "轨道已锁定" : "删除效果"}
                      onPointerDown={(event) => event.stopPropagation()}
                      onClick={(event) => {
                        event.stopPropagation();
                        void deleteEffect(segment);
                      }}
                    >
                      <Trash2 size={10} />
                    </button>
                    {segment.editableRange ? <span className="effect-segment__resize effect-segment__resize--end" data-range-resize="end" aria-label={`调整${label}终点`} /> : null}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
});
