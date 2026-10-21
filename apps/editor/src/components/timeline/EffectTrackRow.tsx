import { tr } from "../../i18n";
import { memo, useEffect, useId, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Sparkles, Trash2 } from "lucide-react";
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
  const [expanded, setExpanded] = useState(false);
  const laneId = useId();
  const disclosure = useRef<HTMLButtonElement | null>(null);
  const selectedEffect = state.selection?.trackId === track.id && state.selection?.effectKind === "effect"
    ? `${state.selection.clipId}:${state.selection.effectId}:${state.selection.effectIndex ?? ""}` : null;
  useEffect(() => {
    if (selectedEffect) setExpanded(true);
  }, [selectedEffect]);
  useEffect(() => {
    const editAppliedEffect = (event: Event) => {
      const target = (event as CustomEvent<{ trackId?: string; category?: string }>).detail;
      if (target?.trackId === track.id && target.category !== "transition") setExpanded(true);
    };
    window.addEventListener("cutvoke:edit-effect", editAppliedEffect);
    return () => window.removeEventListener("cutvoke:edit-effect", editAppliedEffect);
  }, [track.id]);
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

  const rowCount = Math.max(1, ...[...grouped.values()].map((items) => items.length));
  const rowHeight = expanded ? rowCount * 26 + 6 : 16;

  const selectEffect = (segment: EffectSegment, reveal = false) => {
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
    if (reveal) window.dispatchEvent(new CustomEvent("cutvoke:edit-effect", { detail: {
      effectId: String(segment.effect.effectId), category: "fx", trackId: track.id, clipId: segment.clipId,
    } }));
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

  if (segments.length === 0) return null;

  return (
    <div className={`timeline-track timeline-track--effects${expanded ? "" : " timeline-track--effects-collapsed"}${locked ? " timeline-track--locked" : ""}`} style={{ height: rowHeight }}>
      <button
        ref={disclosure}
        type="button"
        className="timeline-track__label timeline-track__label--effects"
        aria-expanded={expanded}
        aria-controls={laneId}
        aria-label={expanded ? tr("收起{0}", displayName) : tr("展开{0}", displayName)}
        title={`${expanded ? tr("收起{0}", displayName) : tr("展开{0}", displayName)} · ${segments.length}${tr(" 个效果")}`}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <span className="timeline-track__kind timeline-track__kind--effect"><Sparkles size={11} /></span>
        <span className="timeline-track__name">{displayName}</span>
        <span className="timeline-track__effect-count">{segments.length}</span>
      </button>
      <div
        id={laneId}
        className="timeline-track__lane timeline-track__lane--effects"
        style={{ height: rowHeight }}
        onClick={() => { if (expanded) dispatch({ type: "SELECTION_SET", selection: { trackId: track.id } }); }}
      >
        {!expanded ? (
          <button
            type="button"
            className="effect-overview"
            aria-label={tr("展开{0}", displayName)}
            aria-expanded={false}
            aria-controls={laneId}
            tabIndex={-1}
            onClick={(event) => {
              event.stopPropagation();
              setExpanded(true);
              disclosure.current?.focus({ preventScroll: true });
            }}
          >
            {[...grouped.entries()].filter(([, items]) =>
              items[0].clipStart + items[0].clipDuration >= viewport.start && items[0].clipStart <= viewport.end,
            ).map(([clipId, items]) => {
              const ranges: { start: number; end: number }[] = [];
              for (const segment of [...items].sort((a, b) => a.localStart - b.localStart)) {
                const previous = ranges[ranges.length - 1];
                if (previous && segment.localStart <= previous.end) previous.end = Math.max(previous.end, segment.localEnd);
                else ranges.push({ start: segment.localStart, end: segment.localEnd });
              }
              return ranges.map((range, index) => (
                <span
                  key={`${clipId}:${index}`}
                  className={`effect-overview__line${selectedEffect && state.selection?.clipId === clipId ? " effect-overview__line--selected" : ""}`}
                  style={{ left: toPx(items[0].clipStart + range.start, pxPerSec), width: Math.max(1, toPx(range.end - range.start, pxPerSec)) }}
                  title={items.map((segment) => effectLabel(effectSpecs, String(segment.effect.effectId))).join(" · ")}
                  aria-hidden="true"
                />
              ));
            })}
          </button>
        ) : (
          <>
        {[...grouped.entries()].filter(([clipId, items]) => track.clips.length <= 100 || clipId === state.selection?.clipId || rangeDraft?.key.startsWith(`${clipId}:`) ||
          (items[0].clipStart + items[0].clipDuration >= viewport.start && items[0].clipStart <= viewport.end)).map(([clipId, items]) => {
          const clipStart = items[0].clipStart;
          const clipDuration = items[0].clipDuration;
          return (
            <div
              key={clipId}
              className="effect-segment-group"
              style={{ left: toPx(clipStart, pxPerSec), width: Math.max(1, toPx(clipDuration, pxPerSec)) }}
            >
              {items.map((segment, row) => {
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
                      width: Math.max(1, toPx(localEnd - localStart, pxPerSec)),
                      top: row * 26,
                      height: 24,
                    }}
                    title={segment.editableRange
                      ? tr("{0} · 拖动调整区间，拖动两侧手柄缩放区间", label)
                      : tr("{0} · 单击选中，Delete 删除", label)}
                    role="group"
                    aria-label={tr("{0}，位于 {1} 到 {2} 秒", label, absoluteStart.toFixed(2), absoluteEnd.toFixed(2))}
                    onClick={(event) => {
                      event.stopPropagation();
                      selectEffect(segment, true);
                    }}
                    onPointerDown={(event) => beginRangeDrag(event, segment)}
                    onPointerMove={moveRangeDrag}
                    onPointerUp={(event) => void finishRangeDrag(event)}
                    onPointerCancel={(event) => void finishRangeDrag(event)}
                  >
                    {segment.editableRange ? <span className="effect-segment__resize effect-segment__resize--start" data-range-resize="start" aria-label={tr("调整{0}起点", label)} /> : null}
                    <button
                      className="effect-segment__select"
                      type="button"
                      aria-pressed={selected}
                      aria-label={tr("{0}，位于 {1} 到 {2} 秒", label, absoluteStart.toFixed(2), absoluteEnd.toFixed(2))}
                      onClick={(event) => {
                        event.stopPropagation();
                        selectEffect(segment, true);
                      }}
                    >
                      <span className="effect-segment__name">{label}</span>
                    </button>
                    <button
                      className="effect-segment__delete"
                      type="button"
                      disabled={locked}
                      aria-label={tr("删除效果 {0}", label)}
                      title={locked ? tr("轨道已锁定") : tr("删除效果")}
                      onPointerDown={(event) => event.stopPropagation()}
                      onClick={(event) => {
                        event.stopPropagation();
                        void deleteEffect(segment);
                      }}
                    >
                      <Trash2 size={10} />
                    </button>
                    {segment.editableRange ? <span className="effect-segment__resize effect-segment__resize--end" data-range-resize="end" aria-label={tr("调整{0}终点", label)} /> : null}
                  </div>
                );
              })}
            </div>
          );
        })}
          </>
        )}
      </div>
    </div>
  );
});
