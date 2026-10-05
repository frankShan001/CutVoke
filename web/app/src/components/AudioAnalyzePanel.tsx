/** 音频分析面板（J09）：对音频文件分析响度/BPM，辅助卡点剪辑。
 *  输入音频路径（或从素材库/工程音频片段选择）→ 调 POST /api/v1/audio/analyze →
 *  展示时长 / 响度 LUFS / BPM。BPM 未检出显示「未检出」。
 *  契约已定，按它写；analyze 端点待后端合并后联调（见汇报 blocking）。 */

import { useMemo, useState } from "react";
import { AudioLines, Activity } from "lucide-react";
import { Button, Field, Panel, Select, TextInput } from "./ui";
import { useEditor, showError } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { analyzeAudio } from "../lib/api";
import { assetDisplayName, useSessionAssets } from "../lib/assetStore";
import { rationalToSecs } from "../lib/rational";
import { curveElapsed, curvePoints } from "../lib/speedCurve";
import { secsToFrameRatString } from "./timeline/util";
import type { AudioAnalysis } from "../lib/api";

export function AudioAnalyzePanel() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const sessionAssets = useSessionAssets();
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AudioAnalysis | null>(null);
  const [beatIndex, setBeatIndex] = useState(0);

  // 可选音频源：会话素材库里的 audio + 工程音频轨道上的 clip 源文件（去重）。
  const audioSources = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of sessionAssets) {
      if (a.kind === "audio" && a.path) map.set(a.path, assetDisplayName(a.path));
    }
    for (const t of state.project?.sequence.tracks || []) {
      if (t.kind !== "audio") continue;
      for (const c of t.clips || []) {
        const p = c.assetRef?.sourcePath;
        if (p && !map.has(p)) map.set(p, assetDisplayName(c.assetRef));
      }
    }
    return [...map.entries()].map(([p, name]) => ({ path: p, name }));
  }, [state.project, sessionAssets]);

  const handleAnalyze = async () => {
    if (!path.trim()) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: "请填写或选择音频路径" });
      return;
    }
    setBusy(true);
    try {
      const res = await analyzeAudio({ path: path.trim() });
      setResult(res);
      dispatch({ type: "STATUS_SET", severity: "ok", text: "音频分析完成" });
    } catch (err) {
      showError(dispatch, err);
    } finally {
      setBusy(false);
    }
  };

  const bpmText =
    result && result.bpm !== null && result.bpm !== undefined
      ? `${result.bpm} BPM`
      : "未检出";

  const clipMatches = (state.project?.sequence.tracks || [])
    .filter((track) => track.kind === "audio")
    .flatMap((track) => track.clips)
    .filter((clip) => clip.assetRef?.sourcePath === path.trim());
  const selectedClip = clipMatches.find((clip) => clip.id === state.selection?.clipId)
    ?? (clipMatches.length === 1 ? clipMatches[0] : null);
  const beatsInClip = selectedClip && result?.beats ? result.beats.flatMap((sourceTime, index) => {
    const sourceStart = rationalToSecs(selectedClip.sourceStart);
    const timelineStart = rationalToSecs(selectedClip.timelineStart);
    const timelineEnd = rationalToSecs(selectedClip.timelineEnd);
    const curve = selectedClip.speedCurve;
    const speed = rationalToSecs(selectedClip.speed ?? { num: "1", den: "1" });
    const sourceSpan = curve ? rationalToSecs(curve.sourceDuration)
      : (timelineEnd - timelineStart) * Math.abs(speed);
    const sourceOffset = sourceTime - sourceStart;
    if (sourceOffset < -0.001 || sourceOffset > sourceSpan + 0.001) return [];
    const elapsed = curve
      ? curveElapsed(sourceOffset, sourceSpan, curvePoints(curve) || [])
      : speed < 0 ? (sourceSpan - sourceOffset) / Math.abs(speed)
        : sourceOffset / speed;
    const timeline = timelineStart + elapsed;
    if (timeline < timelineStart - 0.001 || timeline > timelineEnd + 0.001) return [];
    return [{ index, sourceTime, timeline,
      id: `mk_beat_${selectedClip.id}_${Math.round(sourceTime * 1000)}` }];
  }) : [];
  const selectedBeat = beatsInClip[Math.min(beatIndex, beatsInClip.length - 1)];
  const alreadyMarked = !!selectedBeat && (state.project?.sequence.markers || [])
    .some((marker) => marker.id === selectedBeat.id);

  const markSelectedBeat = async () => {
    if (!selectedClip || !selectedBeat || !state.project) return;
    const res = await runCommand(dispatch, getLatestState() || state, "marker.add", {
      markerId: selectedBeat.id,
      name: `节拍 ${selectedBeat.index + 1} · ${assetDisplayName(path)}`,
      time: secsToFrameRatString(selectedBeat.timeline, state.project.sequence.fps),
    });
    if (res.ok) dispatch({ type: "STATUS_SET", severity: "ok", text: "已在时间线标记节拍；拖动片段边缘可吸附" });
  };

  return (
    <Panel title="音频分析" subtitle="响度与 BPM">
      <Field label="音频源">
        <Select value={path} disabled={busy} onChange={(e) => { setPath(e.target.value); setResult(null); setBeatIndex(0); }} aria-label="选择音频源">
          <option value="">{audioSources.length ? "选择已导入音频…" : "（素材库暂无音频）"}</option>
          {audioSources.map((s) => (
            <option key={s.path} value={s.path}>{s.name}</option>
          ))}
        </Select>
      </Field>
      <Field label="音频路径">
        <TextInput
          value={path}
          onChange={(e) => { setPath(e.target.value); setResult(null); setBeatIndex(0); }}
          disabled={busy}
          placeholder="如：D:\音频\bgm.mp3"
          aria-label="音频路径"
        />
      </Field>
      <Button variant="primary" full onClick={handleAnalyze} disabled={busy} style={{ marginTop: 6 }} aria-label="分析音频">
        <Activity size={14} />
        {busy ? "分析中…" : "分析"}
      </Button>

      {result ? (
        <div className="audio-analyze__result" role="region" aria-label="音频分析结果" style={{ marginTop: 10 }}>
          <div className="audio-analyze__row">
            <AudioLines size={13} />
            <span className="audio-analyze__key">时长</span>
            <span className="audio-analyze__val">{result.duration.toFixed(2)} 秒</span>
          </div>
          <div className="audio-analyze__row">
            <span className="audio-analyze__key">响度</span>
            <span className="audio-analyze__val">{result.loudnessI === null ? "未检出" : result.loudnessI.toFixed(1) + " LUFS"}</span>
          </div>
          <div className="audio-analyze__row">
            <span className="audio-analyze__key">BPM</span>
            <span className="audio-analyze__val">{bpmText}</span>
          </div>
          <div className="audio-analyze__row">
            <span className="audio-analyze__key">起拍点</span>
            <span className="audio-analyze__val">{result.beats?.length || 0} 个候选</span>
          </div>
          {beatsInClip.length ? (
            <div className="audio-analyze__beats">
              <Field label="选择节拍">
                <Select value={Math.min(beatIndex, beatsInClip.length - 1)} onChange={(e) => setBeatIndex(Number(e.target.value))} aria-label="选择节拍">
                  {beatsInClip.map((beat, index) => (
                    <option key={beat.id} value={index}>第 {beat.index + 1} 拍 · 时间线 {beat.timeline.toFixed(3)} 秒</option>
                  ))}
                </Select>
              </Field>
              <div className="audio-analyze__beat-actions">
                <Button onClick={() => selectedBeat && dispatch({ type: "PLAYHEAD_SET", t: selectedBeat.timeline })} aria-label="定位到所选节拍">定位</Button>
                <Button variant="primary" onClick={() => void markSelectedBeat()} disabled={alreadyMarked || !!state.editLock} aria-label="标记所选节拍">
                  {alreadyMarked ? "已标记" : "标记到时间线"}
                </Button>
              </div>
            </div>
          ) : result.beats?.length ? (
            <p className="cv-hint">选择工程中的音频片段后，可将其源素材起拍点映射成时间线标记。</p>
          ) : null}
        </div>
      ) : null}

      <p className="cv-hint" style={{ marginTop: 8 }}>
        起拍点是能量检测候选，标记前请试听复核；时间线标记可用于片段拖动吸附。
      </p>
    </Panel>
  );
}
