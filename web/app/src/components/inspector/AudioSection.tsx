/** 片段音频小节（J01 拆分自 Inspector.tsx）：音量 / 淡入 / 淡出。
 *  拖动/输入只改本地态，提交时才发命令，避免命令风暴刷爆 revision。 */
import { useEffect, useState } from "react";
import { Volume2 } from "lucide-react";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { setClipAudio } from "../../store/clipEdit";
import type { Clip } from "../../types/api";

export function AudioSection({ clip }: { clip: Clip }) {
  const { state, dispatch } = useEditor();
  const [volPct, setVolPct] = useState(100);
  const [fadeInSec, setFadeInSec] = useState(0);
  const [fadeOutSec, setFadeOutSec] = useState(0);

  useEffect(() => {
    setVolPct(Math.round((clip.volume ?? 1) * 100));
    setFadeInSec(clip.fadeIn ?? 0);
    setFadeOutSec(clip.fadeOut ?? 0);
  }, [clip.id, clip.volume, clip.fadeIn, clip.fadeOut]);

  // 提交音频：合并发送 volume/fadeIn/fadeOut（拖动/输入只改本地态，这里才发命令）
  const commitAudio = (vol?: number, fi?: number, fo?: number) => {
    const ctx = getLatestState() || state;
    void setClipAudio(dispatch, ctx, {
      clipId: clip.id,
      volume: vol ?? volPct / 100,
      fadeIn: fi ?? fadeInSec,
      fadeOut: fo ?? fadeOutSec,
    });
  };

  return (
    <div className="inspector__audio">
      <div className="inspector__subrow">
        <span className="inspector__key">
          <Volume2 size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          音频
        </span>
        <span className="inspector__val">{volPct}%</span>
      </div>
      <div className="inspector__audiorow">
        <input
          type="range"
          min={0}
          max={200}
          step={1}
          value={volPct}
          aria-label="片段音量"
          className="cv-range"
          onChange={(e) => setVolPct(Number(e.target.value))}
          onPointerUp={() => commitAudio()}
          onKeyUp={(e) => {
            if (
              ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)
            ) {
              commitAudio();
            }
          }}
        />
      </div>
      <div className="inspector__subrow">
        <span className="inspector__key">淡入</span>
        <input
          type="number"
          step="0.1"
          min="0"
          value={fadeInSec}
          aria-label="淡入时长"
          className="cv-input"
          style={{ width: 56, height: 26 }}
          onChange={(e) => {
            const raw = e.target.value;
            const v = parseFloat(raw);
            setFadeInSec(raw === "" || isNaN(v) ? 0 : v);
          }}
          onBlur={() => commitAudio(undefined, fadeInSec, undefined)}
        />
        <span className="inspector__unit">秒</span>
        <span className="inspector__key">淡出</span>
        <input
          type="number"
          step="0.1"
          min="0"
          value={fadeOutSec}
          aria-label="淡出时长"
          className="cv-input"
          style={{ width: 56, height: 26 }}
          onChange={(e) => {
            const raw = e.target.value;
            const v = parseFloat(raw);
            setFadeOutSec(raw === "" || isNaN(v) ? 0 : v);
          }}
          onBlur={() => commitAudio(undefined, undefined, fadeOutSec)}
        />
        <span className="inspector__unit">秒</span>
      </div>
    </div>
  );
}
