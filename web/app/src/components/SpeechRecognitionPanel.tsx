/** Local ASR workbench: recognize a selected clip, review text, then commit once. */

import { useEffect, useState } from "react";
import { cancelAsrJob, getAsrJob, getAsrStatus, startAsrJob,
  type AsrJob, type AsrStatus } from "../lib/api";
import { rationalToSecs, trySecsToRational } from "../lib/rational";
import { getLatestState, runCommand } from "../store/actions";
import { useEditor } from "../store/editor";

type DraftWord = { text: string; start: string; end: string };
type Draft = {
  index: number;
  text: string;
  start: string;
  end: string;
  words: DraftWord[];
  originStart: string;
  originEnd: string;
};
const jobKey = (projectId: string) => `cutvoke.asr.job.${projectId}`;
const draftKey = (jobId: string) => `cutvoke.asr.drafts.${jobId}`;
const draftsFromJob = (job: AsrJob): Draft[] => (job.result?.segments || []).map((segment) => ({
  index: segment.index,
  text: segment.text,
  start: String(rationalToSecs(segment.start)),
  end: String(rationalToSecs(segment.end)),
  words: (segment.words || []).map((word) => ({
    text: word.text,
    start: String(rationalToSecs(word.start)),
    end: String(rationalToSecs(word.end)),
  })),
  originStart: String(rationalToSecs(segment.start)),
  originEnd: String(rationalToSecs(segment.end)),
}));
const validDraftWords = (value: unknown): value is DraftWord[] => Array.isArray(value)
  && value.every((word) => word != null && typeof word === "object"
    && typeof word.text === "string" && typeof word.start === "string"
    && typeof word.end === "string");
const restoredDrafts = (saved: string | null, job: AsrJob): Draft[] => {
  if (saved == null) return draftsFromJob(job);
  try {
    const parsed: unknown = JSON.parse(saved);
    const originals = new Map(draftsFromJob(job).map((draft) => [draft.index, draft]));
    const allowed = new Set(originals.keys());
    if (Array.isArray(parsed) && parsed.every((item) =>
      item != null && typeof item === "object" &&
      Number.isInteger(item.index) && allowed.has(item.index) &&
      typeof item.text === "string" && typeof item.start === "string" &&
      typeof item.end === "string" &&
      (item.words === undefined || validDraftWords(item.words)))) {
      return (parsed as Partial<Draft>[]).map((item) => {
        const original = originals.get(item.index as number)!;
        const sameAsSource = item.text === original.text
          && item.start === original.start && item.end === original.end;
        return {
          index: item.index as number,
          text: item.text as string,
          start: item.start as string,
          end: item.end as string,
          words: validDraftWords(item.words) ? item.words : sameAsSource ? original.words : [],
          originStart: typeof item.originStart === "string" ? item.originStart : original.originStart,
          originEnd: typeof item.originEnd === "string" ? item.originEnd : original.originEnd,
        };
      });
    }
  } catch { /* An interrupted browser write should not hide recognition results. */ }
  return draftsFromJob(job);
};

function rebaseDraftWords(draft: Draft, start: string, end: string): DraftWord[] {
  if (!draft.words.length) return [];
  const originalStart = Number(draft.originStart);
  const originalEnd = Number(draft.originEnd);
  const nextStart = Number(start);
  const nextEnd = Number(end);
  if (!Number.isFinite(originalStart) || !Number.isFinite(originalEnd)
      || !Number.isFinite(nextStart) || !Number.isFinite(nextEnd)
      || originalEnd <= originalStart || nextEnd <= nextStart) return draft.words;
  const ratio = (nextEnd - nextStart) / (originalEnd - originalStart);
  return draft.words.map((word) => ({
    ...word,
    start: String(nextStart + (Number(word.start) - originalStart) * ratio),
    end: String(nextStart + (Number(word.end) - originalStart) * ratio),
  }));
}

export function SpeechRecognitionPanel() {
  const { state, dispatch } = useEditor();
  const [capability, setCapability] = useState<AsrStatus | null>(null);
  const [model, setModel] = useState("base");
  const [language, setLanguage] = useState("auto");
  const [job, setJob] = useState<AsrJob | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [wordHighlightEnabled, setWordHighlightEnabled] = useState(false);
  const [wordHighlightColor, setWordHighlightColor] = useState("#FFD54A");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const readOnly = Boolean(state.editLock);
  const selectedTrack = state.project?.sequence.tracks.find(
    (track) => track.id === state.selection?.trackId,
  );
  const selectedClip = selectedTrack?.clips.find((clip) => clip.id === state.selection?.clipId);
  const canRecognize = selectedClip != null &&
    (selectedTrack?.kind === "video" || selectedTrack?.kind === "audio");
  const selectedName = selectedClip?.assetRef.sourcePath.split(/[\\/]/).pop() || "";

  useEffect(() => {
    let active = true;
    getAsrStatus().then((status) => {
      if (active) setCapability(status);
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : String(cause));
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setJob(null);
    setDrafts([]);
    setError("");
    const projectId = state.currentId;
    if (projectId) {
      const oldJobId = window.sessionStorage.getItem(jobKey(projectId));
      if (oldJobId) {
        getAsrJob(oldJobId).then((restored) => {
          if (!active) return;
          setJob(restored);
          if (restored.status === "completed") {
            const saved = window.sessionStorage.getItem(draftKey(restored.jobId));
            setDrafts(restoredDrafts(saved, restored));
          }
        }).catch(() => {
          if (active) {
            window.sessionStorage.removeItem(jobKey(projectId));
            setError("上次识别任务已不可用，请重新识别");
          }
        });
      }
    }
    return () => { active = false; };
  }, [state.currentId]);

  useEffect(() => {
    if (job?.status === "completed") {
      window.sessionStorage.setItem(draftKey(job.jobId), JSON.stringify(drafts));
    }
  }, [job?.jobId, job?.status, drafts]);

  useEffect(() => {
    if (!job || (job.status !== "queued" && job.status !== "running")) return;
    let active = true;
    const poll = async () => {
      try {
        const next = await getAsrJob(job.jobId);
        if (!active) return;
        setJob(next);
        if (next.status === "completed") {
          setDrafts(draftsFromJob(next));
        }
        if (next.status === "failed") setError(next.error || "语音识别失败");
      } catch (cause) {
        if (active) {
          const message = cause instanceof Error ? cause.message : String(cause);
          setError(message);
          setJob((current) => current ? { ...current, status: "failed",
                                      phase: "识别任务不可用", error: message } : current);
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 700);
    return () => { active = false; window.clearInterval(timer); };
  }, [job?.jobId, job?.status]);

  const start = async (clipId?: string) => {
    const target = clipId || (canRecognize ? selectedClip?.id : undefined);
    if (!state.currentId || !target || busy || job?.status === "completed") return;
    setError("");
    setBusy(true);
    try {
      const next = await startAsrJob(state.currentId, { clipId: target, model, language });
      setJob(next);
      setDrafts([]);
      window.sessionStorage.setItem(jobKey(state.currentId), next.jobId);
      window.sessionStorage.removeItem(draftKey(next.jobId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const edit = (index: number, patch: Partial<Draft>) => {
    setDrafts((current) => current.map((draft) => {
      if (draft.index !== index) return draft;
      const next = { ...draft, ...patch };
      if (patch.text !== undefined && patch.text !== draft.text) {
        return { ...next, words: [] };
      }
      if (patch.start !== undefined || patch.end !== undefined) {
        const words = rebaseDraftWords(draft, next.start, next.end);
        const validRange = Number.isFinite(Number(next.start))
          && Number.isFinite(Number(next.end))
          && Number(next.end) > Number(next.start);
        return {
          ...next,
          words,
          ...(validRange ? { originStart: next.start, originEnd: next.end } : {}),
        };
      }
      return next;
    }));
  };

  const discard = async () => {
    if (!job || !state.currentId || busy) return;
    setBusy(true);
    let cancelError = "";
    try {
      if (job.status === "queued" || job.status === "running") {
        await cancelAsrJob(job.jobId);
      }
    } catch (cause) {
      cancelError = cause instanceof Error ? cause.message : String(cause);
    } finally {
      window.sessionStorage.removeItem(jobKey(state.currentId));
      window.sessionStorage.removeItem(draftKey(job.jobId));
      setJob(null);
      setDrafts([]);
      setError(cancelError);
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!job?.result || !drafts.length || !state.currentId || busy) return;
    const segments = [];
    for (const draft of drafts) {
      const startTime = trySecsToRational(draft.start);
      const endTime = trySecsToRational(draft.end);
      if (!draft.text.trim() || !startTime || !endTime ||
          rationalToSecs(endTime) <= rationalToSecs(startTime)) {
        setError(`第 ${draft.index} 条字幕的文字或时间无效`);
        return;
      }
      const words = [];
      for (const word of draft.words) {
        const wordStart = trySecsToRational(word.start);
        const wordEnd = trySecsToRational(word.end);
        if (!word.text.trim() || !wordStart || !wordEnd
            || rationalToSecs(wordEnd) <= rationalToSecs(wordStart)) {
          setError(`第 ${draft.index} 条字幕的词级时间无效，请调整时码或重新识别`);
          return;
        }
        words.push({ text: word.text, start: wordStart, end: wordEnd });
      }
      segments.push({ text: draft.text.trim(), start: startTime, end: endTime, words });
    }
    setBusy(true);
    setError("");
    const result = await runCommand(dispatch, getLatestState() || state, "caption.bulkAdd", {
      segments, sourceClipId: job.clipId,
      sourceSignature: job.result.sourceSignature,
      style: { wordHighlightColor: wordHighlightEnabled ? wordHighlightColor : "" },
    });
    setBusy(false);
    if (result.ok) {
      dispatch({ type: "STATUS_SET", severity: "ok",
                 text: `已添加 ${segments.length} 条识别字幕，可逐条编辑和撤销` });
      setJob(null);
      setDrafts([]);
      setQuery("");
      window.sessionStorage.removeItem(jobKey(state.currentId));
      window.sessionStorage.removeItem(draftKey(job.jobId));
    } else if (result.error) {
      setError(result.error.message);
    }
  };

  const visibleDrafts = drafts.filter((draft) =>
    draft.text.toLowerCase().includes(query.trim().toLowerCase()));
  const wordTimeCount = drafts.reduce((total, draft) => total + draft.words.length, 0);

  return (
    <section className="cv-asr" aria-label="自动字幕">
      <div className="cv-asr__heading">
        <strong>自动字幕 · 本地语音识别</strong>
        <span className="cv-hint">先识别，再检查并一次添加到工程</span>
      </div>
      <p className="cv-hint">{capability?.message || "正在检查识别组件…"}</p>
      {capability?.devicePreference ? (
        <p className="cv-hint">
          运行设备：{capability.devicePreference === "cpu"
            ? "强制 CPU（int8）"
            : capability.devicePreference === "cuda"
              ? "强制 CUDA（float16）"
              : capability.devicePreference === "auto"
                ? "自动选择 GPU；初始化失败时回退 CPU"
                : `配置无效（${capability.devicePreference}）`}
        </p>
      ) : null}
      <p className="cv-hint">{canRecognize
        ? `当前片段：${selectedName}` : "请先在时间线选中有声视频或音频片段"}</p>
      <div className="cv-asr__settings">
        <label>语言
          <select className="cv-input" aria-label="识别语言" value={language}
            onChange={(event) => setLanguage(event.target.value)} disabled={busy || readOnly}>
            <option value="auto">自动检测</option>
            <option value="zh">中文</option>
            <option value="en">英语</option>
            <option value="ja">日语</option>
            <option value="ko">韩语</option>
          </select>
        </label>
        <label>模型
          <select className="cv-input" aria-label="识别模型" value={model}
            onChange={(event) => setModel(event.target.value)} disabled={busy || readOnly}>
            <option value="tiny">Tiny · 快速校验</option>
            <option value="base">Base · 默认</option>
            <option value="small">Small · 更准确</option>
            <option value="medium">Medium · 更准确且较慢</option>
          </select>
        </label>
      </div>
      <button className="cv-btn cv-btn--primary" type="button"
        onClick={() => void start()} disabled={!capability?.ready || !canRecognize || busy || readOnly ||
          job?.status === "running" || job?.status === "queued" || job?.status === "completed"}>
        开始识别
      </button>
      {job ? (
        <div className="cv-asr__job" role="status">
          <span>{job.phase} · {job.progress}%</span>
          <progress max={100} value={job.progress} aria-label="语音识别进度" />
          {job.status === "failed" ? (
            <button className="cv-btn cv-btn--secondary" type="button"
              onClick={() => void start(job.clipId)} disabled={busy || readOnly}>重试识别</button>
          ) : null}
          <button className="cv-btn cv-btn--ghost cv-btn--sm" type="button"
            onClick={() => void discard()} disabled={busy}>
            {job.status === "queued" || job.status === "running" ? "取消识别" : "放弃识别结果"}
          </button>
        </div>
      ) : null}
      {error ? <p className="cv-asr__error" role="alert">{error}</p> : null}
      {job?.status === "completed" ? (
        <div className="cv-asr__review">
          <strong>{[
            `识别结果：${drafts.length} 条`,
            `语言 ${job.result?.language || "未知"}`,
            job.result?.model || job.model,
            `${job.result?.device === "cuda" ? "GPU" : job.result?.device === "cpu" ? "CPU" : "设备未知"}${job.result?.computeType ? ` ${job.result.computeType}` : ""}`,
          ].join(" · ")}</strong>
          {drafts.length ? (
            <>
              <input className="cv-input" aria-label="搜索识别结果" placeholder="搜索识别文字"
                value={query} onChange={(event) => setQuery(event.target.value)} />
              <div className="cv-asr__drafts">
                {visibleDrafts.map((draft) => (
                  <div className="cv-asr__draft" key={draft.index}>
                    <div className="cv-asr__draft-times">
                      <input className="cv-input" type="number" step="any"
                        aria-label={`第 ${draft.index} 条开始时间`} value={draft.start}
                        onChange={(event) => edit(draft.index, { start: event.target.value })}
                        disabled={readOnly} />
                      <span>—</span>
                      <input className="cv-input" type="number" step="any"
                        aria-label={`第 ${draft.index} 条结束时间`} value={draft.end}
                        onChange={(event) => edit(draft.index, { end: event.target.value })}
                        disabled={readOnly} />
                    </div>
                    <textarea className="cv-input" rows={2} value={draft.text}
                      aria-label={`第 ${draft.index} 条识别文字`}
                      onChange={(event) => edit(draft.index, { text: event.target.value })}
                      disabled={readOnly} />
                    <button className="cv-btn cv-btn--ghost cv-btn--sm" type="button"
                      onClick={() => setDrafts((current) => current.filter((item) => item.index !== draft.index))}
                      disabled={readOnly}>移除这条</button>
                  </div>
                ))}
              </div>
              <div className="cv-asr__word-highlight">
                <label>
                  <input type="checkbox" aria-label="逐词高亮"
                    checked={wordHighlightEnabled}
                    onChange={(event) => setWordHighlightEnabled(event.target.checked)}
                    disabled={readOnly || wordTimeCount === 0} />
                  逐词高亮
                </label>
                <input type="color" className="cv-input" aria-label="逐词高亮颜色"
                  value={wordHighlightColor}
                  onChange={(event) => setWordHighlightColor(event.target.value)}
                  disabled={readOnly || !wordHighlightEnabled || wordTimeCount === 0} />
                <span className="cv-hint">
                  {wordTimeCount
                    ? `这批结果有 ${wordTimeCount} 个词级时间点；改文字会清除对应时间点，改时码会同步缩放。`
                    : "本批识别结果没有可用的词级时间点。"}
                </span>
              </div>
              <button className="cv-btn cv-btn--primary" type="button" onClick={() => void apply()}
                disabled={busy || readOnly}>添加 {drafts.length} 条到工程</button>
            </>
          ) : <p className="cv-hint">未识别到可用语音，可检查原声或选择其它模型后重试。</p>}
        </div>
      ) : null}
    </section>
  );
}
