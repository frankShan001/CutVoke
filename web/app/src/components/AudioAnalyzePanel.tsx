/** 音频分析面板（J09）：对音频文件分析响度/BPM，辅助卡点剪辑。
 *  输入音频路径（或从素材库/工程音频片段选择）→ 调 POST /api/v1/audio/analyze →
 *  展示时长 / 响度 LUFS / BPM。BPM 未检出显示「未检出」。
 *  契约已定，按它写；analyze 端点待后端合并后联调（见汇报 blocking）。 */

import { useMemo, useState } from "react";
import { AudioLines, Activity } from "lucide-react";
import { Button, Field, Panel, Select, TextInput } from "./ui";
import { useEditor, showError } from "../store/editor";
import { analyzeAudio } from "../lib/api";
import { getSessionAssets } from "../lib/assetStore";
import { sourceBasename } from "../lib/media";
import type { AudioAnalysis } from "../lib/api";

export function AudioAnalyzePanel() {
  const { state, dispatch } = useEditor();
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AudioAnalysis | null>(null);

  // 可选音频源：会话素材库里的 audio + 工程音频轨道上的 clip 源文件（去重）。
  const audioSources = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of getSessionAssets()) {
      if (a.kind === "audio" && a.path) map.set(a.path, a.name || sourceBasename(a.path));
    }
    for (const t of state.project?.sequence.tracks || []) {
      if (t.kind !== "audio") continue;
      for (const c of t.clips || []) {
        const p = c.assetRef?.sourcePath;
        if (p && !map.has(p)) map.set(p, sourceBasename(p));
      }
    }
    return [...map.entries()].map(([p, name]) => ({ path: p, name }));
  }, [state.project]);

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

  return (
    <Panel title="音频分析" subtitle="响度与 BPM">
      <Field label="音频源">
        <Select value={path} onChange={(e) => setPath(e.target.value)} aria-label="选择音频源">
          <option value="">{audioSources.length ? "选择已导入音频…" : "（素材库暂无音频）"}</option>
          {audioSources.map((s) => (
            <option key={s.path} value={s.path}>{s.name}</option>
          ))}
        </Select>
      </Field>
      <Field label="音频路径">
        <TextInput
          value={path}
          onChange={(e) => setPath(e.target.value)}
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
            <span className="audio-analyze__val">{result.loudnessI.toFixed(1)} LUFS</span>
          </div>
          <div className="audio-analyze__row">
            <span className="audio-analyze__key">BPM</span>
            <span className="audio-analyze__val">{bpmText}</span>
          </div>
        </div>
      ) : null}

      <p className="cv-hint" style={{ marginTop: 8 }}>
        分析音频的响度与节奏，帮助对齐卡点。BPM 未检出时将显示「未检出」。
      </p>
    </Panel>
  );
}
