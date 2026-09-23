import { Sparkles, Trash2 } from "lucide-react";
import type { EffectInstance, Track } from "../../types/api";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { removeEffect } from "../../store/clipEdit";
import { effectLabel, type EffectSpec } from "../../lib/effects";
import { isTransitionEffectId } from "../../lib/transitions";
import { clipEndSecs, clipStartSecs, toPx } from "./util";

interface EffectSegment {
  clipId: string;
  effect: EffectInstance;
  index: number;
  start: number;
  end: number;
}

export function EffectTrackRow({
  track,
  displayName,
  pxPerSec,
  effectSpecs,
}: {
  track: Track;
  displayName: string;
  pxPerSec: number;
  effectSpecs: EffectSpec[];
}) {
  const { state, dispatch } = useEditor();
  const locked = !!track.locked || !!state.editLock;
  const segments: EffectSegment[] = track.clips.flatMap((clip) =>
    (clip.effects || [])
      .map((effect, index) => ({
        clipId: clip.id,
        effect,
        index,
        start: clipStartSecs(clip),
        end: clipEndSecs(clip),
      }))
      .filter((item) => !isTransitionEffectId(String(item.effect.effectId))),
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
        effectKind: "effect",
      },
    });
    dispatch({ type: "PLAYHEAD_SET", t: segment.start });
  };

  const deleteEffect = async (segment: EffectSegment) => {
    if (locked) return;
    const result = await removeEffect(dispatch, getLatestState() || state, {
      clipId: segment.clipId,
      effectId: String(segment.effect.effectId),
    });
    if (result.ok) dispatch({ type: "SELECTION_SET", selection: null });
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
        {[...grouped.entries()].map(([clipId, items]) => {
          const start = items[0].start;
          const end = items[0].end;
          return (
            <div
              key={clipId}
              className="effect-segment-group"
              style={{ left: toPx(start, pxPerSec), width: Math.max(12, toPx(end - start, pxPerSec)) }}
            >
              {items.map((segment) => {
                const id = String(segment.effect.effectId);
                const label = effectLabel(effectSpecs, id);
                const selected =
                  state.selection?.trackId === track.id &&
                  state.selection?.clipId === segment.clipId &&
                  state.selection?.effectId === id &&
                  state.selection?.effectKind === "effect";
                return (
                  <div
                    key={`${id}-${segment.index}`}
                    className={`effect-segment${selected ? " effect-segment--selected" : ""}`}
                    title={`${label} · 单击选中，Delete 删除`}
                  >
                    <button
                      className="effect-segment__select"
                      type="button"
                      aria-pressed={selected}
                      aria-label={`${label}，位于 ${start.toFixed(2)} 到 ${end.toFixed(2)} 秒`}
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
                      onClick={(event) => {
                        event.stopPropagation();
                        void deleteEffect(segment);
                      }}
                    >
                      <Trash2 size={10} />
                    </button>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
