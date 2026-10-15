import { tr, useLocale } from "../../i18n";
import { useMemo } from "react";
import { Captions } from "lucide-react";
import { useEditor } from "../../store/editor";
import { rationalToSecs } from "../../lib/rational";
import type { Caption } from "../../types/api";
import { fmtTimePrecise, toPx } from "./util";

interface CaptionLaneItem {
  caption: Caption;
  start: number;
  end: number;
  lane: number;
}

/** Captions stay independently editable while their timing is visible beside media and effects. */
export function CaptionTrackRow({
  captions,
  selectedCaptionId,
  pxPerSec,
}: {
  captions: Caption[];
  selectedCaptionId: string | null;
  pxPerSec: number;
}) {
  const locale = useLocale();
  const { dispatch } = useEditor({ subscribeToClock: false });
  const layout = useMemo(() => {
    const sorted = captions.map((caption) => ({
      caption,
      start: rationalToSecs(caption.start),
      end: rationalToSecs(caption.end),
    })).sort((a, b) => a.start - b.start || a.end - b.end || a.caption.id.localeCompare(b.caption.id));
    const laneEnds: number[] = [];
    const items: CaptionLaneItem[] = sorted.map((item) => {
      let lane = laneEnds.findIndex((end) => end <= item.start + 1e-6);
      if (lane < 0) lane = laneEnds.length;
      laneEnds[lane] = item.end;
      return { ...item, lane };
    });
    return { items, height: Math.max(36, laneEnds.length * 18 + 8) };
  }, [captions, locale]);

  return (
    <div className="timeline-track timeline-track--captions" data-testid="caption-timeline-track"
      style={{ height: layout.height }}>
      <div className="timeline-track__label timeline-track__label--captions">
        <Captions size={12} aria-hidden="true" />
        <span>{tr("字幕")}</span>
        <span className="timeline-track__caption-count">{captions.length}</span>
      </div>
      <div className="timeline-track__lane timeline-track__lane--captions" style={{ height: layout.height }}>
        {layout.items.map(({ caption, start, end, lane }) => {
          const selected = caption.id === selectedCaptionId;
          return (
            <button
              key={caption.id}
              type="button"
              className={`caption-timeline-clip${selected ? " caption-timeline-clip--selected" : ""}`}
              data-testid="caption-timeline-clip"
              data-caption-id={caption.id}
              style={{ left: toPx(start, pxPerSec), width: Math.max(18, toPx(end - start, pxPerSec)), top: 4 + lane * 18 }}
              aria-pressed={selected}
              aria-label={tr("字幕：{0}，{1} 到 {2}", caption.text, fmtTimePrecise(start), fmtTimePrecise(end))}
              title={tr("{0} · {1}–{2} · 点击定位并选择字幕", caption.text, fmtTimePrecise(start), fmtTimePrecise(end))}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                dispatch({ type: "CAPTION_SELECTION_SET", captionId: caption.id });
                dispatch({ type: "PLAYHEAD_SET", t: start });
              }}
            >
              <span>{caption.text || tr("（空字幕）")}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
