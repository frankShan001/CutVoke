import { msg, tr, useLocale } from "../i18n";
import { backendMessage } from "../i18n/backendMessages";
/** Local ASR workbench: recognize a selected clip, review text, then commit once. */

import { useEffect, useState } from "react";
import { dismissAsrJob, getAsrJob, getAsrStatus, listAsrJobs, resumeAsrJob, saveAsrDrafts, startAsrJob,
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

type CachedDrafts = { drafts: Draft[]; updatedAt: number; pending: boolean };
function cachedDrafts(jobId: string): CachedDrafts | null {
  try {
    const raw = window.localStorage.getItem(draftKey(jobId))
      || window.sessionStorage.getItem(draftKey(jobId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Older sessions saved the array directly, without a server acknowledgement.
    if (Array.isArray(parsed)) return { drafts: parsed, updatedAt: 0, pending: true };
    if (parsed && Array.isArray(parsed.drafts) && Number.isSafeInteger(parsed.updatedAt)
        && typeof parsed.pending === "boolean") return parsed;
  } catch { /* The server journal remains usable when browser storage is unavailable. */ }
  return null;
}

function storeDrafts(jobId: string, value: CachedDrafts) {
  const encoded = JSON.stringify(value);
  try { window.localStorage.setItem(draftKey(jobId), encoded); } catch { /* Server save remains available. */ }
  try { window.sessionStorage.setItem(draftKey(jobId), encoded); } catch { /* Use the durable browser copy. */ }
}

function retainDrafts(job: AsrJob, drafts: Draft[]): CachedDrafts {
  const cached = cachedDrafts(job.jobId);
  if (cached && JSON.stringify(cached.drafts) === JSON.stringify(drafts)) return cached;
  const value = { drafts, updatedAt: Math.max(Date.now() * 1000,
    (cached?.updatedAt || 0) + 1, (job.draftsUpdatedAt || 0) + 1), pending: true };
  storeDrafts(job.jobId, value);
  return value;
}

function restoreJobDrafts(job: AsrJob): Draft[] {
  const cached = cachedDrafts(job.jobId);
  const localIsNewer = cached?.pending && cached.updatedAt >= (job.draftsUpdatedAt || 0);
  const selected = localIsNewer ? cached.drafts : job.drafts ?? cached?.drafts;
  return restoredDrafts(selected != null ? JSON.stringify(selected) : null, job);
}

function forgetDrafts(jobId: string) {
  try { window.localStorage.removeItem(draftKey(jobId)); } catch { /* Best effort. */ }
  try { window.sessionStorage.removeItem(draftKey(jobId)); } catch { /* Best effort. */ }
}

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
  useLocale();
  const { state, dispatch } = useEditor();
  const [capability, setCapability] = useState<AsrStatus | null>(null);
  const [model, setModel] = useState("base");
  const [language, setLanguage] = useState("auto");
  const [job, setJob] = useState<AsrJob | null>(null);
  const [historyJobs, setHistoryJobs] = useState<AsrJob[]>([]);
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
    setHistoryJobs([]);
    setDrafts([]);
    setError("");
    const projectId = state.currentId;
    if (projectId) {
      listAsrJobs(projectId).then(({ jobs }) => {
          // The project journal also works after an app restart or an origin change.
          const oldJobId = window.sessionStorage.getItem(jobKey(projectId));
          const restored = jobs.find((candidate) => candidate.jobId === oldJobId) || jobs[0];
          if (!active) return;
          setHistoryJobs(jobs);
          if (!restored) return;
          setJob(restored);
          setModel(restored.model);
          setLanguage(restored.requestedLanguage);
          window.sessionStorage.setItem(jobKey(projectId), restored.jobId);
          if (restored.status === "completed") {
            setDrafts(restoreJobDrafts(restored));
          }
          if (restored.status === "failed") setError(restored.error);
        }).catch((cause) => {
          if (active) {
            setError(cause instanceof Error ? cause.message : String(cause));
          }
        });
    }
    return () => { active = false; };
  }, [state.currentId]);

  useEffect(() => {
    if (job?.status === "completed") {
      const saved = retainDrafts(job, drafts);
      if (!saved.pending) return;
      const timer = window.setTimeout(() => {
        saveAsrDrafts(job.jobId, drafts, saved.updatedAt).then((result) => {
          const current = cachedDrafts(job.jobId);
          // A reply for an older edit must not acknowledge a newer local draft.
          if (current?.updatedAt === saved.updatedAt && result.draftsUpdatedAt === saved.updatedAt) {
            storeDrafts(job.jobId, { ...current, pending: false });
          }
        }).catch((cause) => {
          setError(cause instanceof Error ? cause.message : String(cause));
        });
      }, 350);
      return () => window.clearTimeout(timer);
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
          setDrafts(restoreJobDrafts(next));
        }
        if (next.status === "failed") setError(next.error || tr("语音识别失败"));
      } catch (cause) {
        if (active) {
          const message = cause instanceof Error ? cause.message : String(cause);
          setError(message);
          // A service restart is temporary. Keep polling the durable job ID.
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
      setDrafts(next.status === "completed" ? draftsFromJob(next) : []);
      setHistoryJobs((current) => [next, ...current.filter((candidate) => candidate.jobId !== next.jobId)]);
      window.sessionStorage.setItem(jobKey(state.currentId), next.jobId);
      forgetDrafts(next.jobId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const setReviewedDrafts = (next: Draft[]) => {
    // Persist in the input event, before a reload/unmount can cancel the debounce.
    if (job?.status === "completed") retainDrafts(job, next);
    setDrafts(next);
  };

  const edit = (index: number, patch: Partial<Draft>) => {
    setReviewedDrafts(drafts.map((draft) => {
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
    try {
      await dismissAsrJob(job.jobId);
      window.sessionStorage.removeItem(jobKey(state.currentId));
      forgetDrafts(job.jobId);
      setJob(null);
      setHistoryJobs((current) => current.filter((candidate) => candidate.jobId !== job.jobId));
      setDrafts([]);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const selectJob = async (jobId: string) => {
    if (!state.currentId || busy) return;
    setBusy(true);
    setError("");
    try {
      if (job?.status === "completed") {
        const saved = retainDrafts(job, drafts);
        await saveAsrDrafts(job.jobId, drafts, saved.updatedAt);
      }
      const next = await getAsrJob(jobId);
      setJob(next);
      setModel(next.model);
      setLanguage(next.requestedLanguage);
      setDrafts(next.status === "completed" ? restoreJobDrafts(next) : []);
      window.sessionStorage.setItem(jobKey(state.currentId), next.jobId);
      if (next.status === "failed") setError(next.error);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const resume = async () => {
    if (!job || busy || readOnly) return;
    setBusy(true);
    setError("");
    try {
      const next = await resumeAsrJob(job.jobId);
      setJob(next);
      if (next.status === "failed") setError(next.error);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
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
        setError(tr("第 {0} 条字幕的文字或时间无效", draft.index));
        return;
      }
      const words = [];
      for (const word of draft.words) {
        const wordStart = trySecsToRational(word.start);
        const wordEnd = trySecsToRational(word.end);
        if (!word.text.trim() || !wordStart || !wordEnd
            || rationalToSecs(wordEnd) <= rationalToSecs(wordStart)) {
          setError(tr("第 {0} 条字幕的词级时间无效，请调整时码或重新识别", draft.index));
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
    if (result.ok) {
      // Retire the proposal so a later launch does not offer already-added text.
      await dismissAsrJob(job.jobId).catch(() => {});
      dispatch({ type: "STATUS_SET", severity: "ok",
                 text: msg("已添加 {0} 条识别字幕，可逐条编辑和撤销", segments.length) });
      setJob(null);
      setHistoryJobs((current) => current.filter((candidate) => candidate.jobId !== job.jobId));
      setDrafts([]);
      setQuery("");
      window.sessionStorage.removeItem(jobKey(state.currentId));
      forgetDrafts(job.jobId);
    } else if (result.error) {
      setError(result.error.message);
    }
    setBusy(false);
  };

  const visibleDrafts = drafts.filter((draft) =>
    draft.text.toLowerCase().includes(query.trim().toLowerCase()));
  const wordTimeCount = drafts.reduce((total, draft) => total + draft.words.length, 0);

  return (
    <section className="cv-asr" aria-label={tr("自动字幕")}>
      <div className="cv-asr__heading">
        <strong>{tr("自动字幕 · 本地语音识别")}</strong>
        <span className="cv-hint">{tr("先识别，再检查并一次添加到工程")}</span>
      </div>
      <p className="cv-hint">{capability?.message ? backendMessage(capability.message) : tr("正在检查识别组件…")}</p>
      {capability?.devicePreference ? (
        <p className="cv-hint">{tr("运行设备：")}{capability.devicePreference === "cpu"
            ? tr("强制 CPU（int8）")
            : capability.devicePreference === "cuda"
              ? tr("强制 CUDA（float16）")
              : capability.devicePreference === "auto"
                ? tr("自动选择 GPU；初始化失败时回退 CPU")
                : tr("配置无效（{0}）", capability.devicePreference)}
        </p>
      ) : null}
      <p className="cv-hint">{canRecognize
        ? tr("当前片段：{0}", selectedName) : tr("请先在时间线选中有声视频或音频片段")}</p>
      {historyJobs.length > 1 || (!job && historyJobs.length > 0) ? (
        <label>{tr("识别任务")}<select className="cv-input" aria-label={tr("识别任务")}
          value={job?.jobId || ""} disabled={busy}
          onChange={(event) => void selectJob(event.target.value)}>
          {!job ? <option value="" disabled>{tr("识别任务")}</option> : null}
          {historyJobs.map((candidate) => <option key={candidate.jobId} value={candidate.jobId}>
            {candidate.clipId} · {candidate.model} · {candidate.requestedLanguage}
          </option>)}
        </select></label>
      ) : null}
      <div className="cv-asr__settings">
        <label>{tr("语言")}<select className="cv-input" aria-label={tr("识别语言")} value={language}
            onChange={(event) => setLanguage(event.target.value)} disabled={busy || readOnly}>
            <option value="auto">{tr("自动检测")}</option>
            <option value="zh">{tr("中文")}</option>
            <option value="en">{tr("英语")}</option>
            <option value="ja">{tr("日语")}</option>
            <option value="ko">{tr("韩语")}</option>
          </select>
        </label>
        <label>{tr("模型")}<select className="cv-input" aria-label={tr("识别模型")} value={model}
            onChange={(event) => setModel(event.target.value)} disabled={busy || readOnly}>
            <option value="tiny">{tr("Tiny · 快速校验")}</option>
            <option value="base">{tr("Base · 默认")}</option>
            <option value="small">{tr("Small · 更准确")}</option>
            <option value="medium">{tr("Medium · 更准确且较慢")}</option>
          </select>
        </label>
      </div>
      <button className="cv-btn cv-btn--primary" type="button"
        onClick={() => void start()} disabled={!capability?.ready || !canRecognize || busy || readOnly ||
          job?.status === "running" || job?.status === "queued" || job?.status === "completed" ||
          job?.status === "interrupted"}>{tr("开始识别")}</button>
      {job ? (
        <div className="cv-asr__job" role="status">
          <span>{backendMessage(job.phase)} · {job.progress}%</span>
          <progress max={100} value={job.progress} aria-label={tr("语音识别进度")} />
          {job.status === "interrupted" ? (
            <button className="cv-btn cv-btn--secondary" type="button"
              onClick={() => void resume()} disabled={busy || readOnly || !capability?.ready}>{tr("继续识别")}</button>
          ) : null}
          {job.status === "failed" ? (
            <button className="cv-btn cv-btn--secondary" type="button"
              onClick={() => void start(job.clipId)} disabled={busy || readOnly}>{tr("重试识别")}</button>
          ) : null}
          <button className="cv-btn cv-btn--ghost cv-btn--sm" type="button"
            onClick={() => void discard()} disabled={busy}>
            {job.status === "queued" || job.status === "running" ? tr("取消识别") : tr("放弃识别结果")}
          </button>
        </div>
      ) : null}
      {error ? <p className="cv-asr__error" role="alert"><strong>{tr("语音识别未完成，请检查下面的提示。")}</strong><br /><span dir="auto">{error}</span></p> : null}
      {job?.status === "completed" ? (
        <div className="cv-asr__review">
          <strong>{[
            tr("识别结果：{0} 条", drafts.length),
            tr("语言 {0}", job.result?.language || "未知"),
            job.result?.model || job.model,
            tr("{0}{1}", job.result?.device === "cuda" ? "GPU" : job.result?.device === "cpu" ? "CPU" : tr("设备未知"), job.result?.computeType ? tr(" {0}", job.result.computeType) : ""),
          ].join(" · ")}</strong>
          {drafts.length ? (
            <>
              <input className="cv-input" aria-label={tr("搜索识别结果")} placeholder={tr("搜索识别文字")}
                value={query} onChange={(event) => setQuery(event.target.value)} />
              <div className="cv-asr__drafts">
                {visibleDrafts.map((draft) => (
                  <div className="cv-asr__draft" key={draft.index}>
                    <div className="cv-asr__draft-times">
                      <input className="cv-input" type="number" step="any"
                        aria-label={tr("第 {0} 条开始时间", draft.index)} value={draft.start}
                        onChange={(event) => edit(draft.index, { start: event.target.value })}
                        disabled={readOnly} />
                      <span>—</span>
                      <input className="cv-input" type="number" step="any"
                        aria-label={tr("第 {0} 条结束时间", draft.index)} value={draft.end}
                        onChange={(event) => edit(draft.index, { end: event.target.value })}
                        disabled={readOnly} />
                    </div>
                    <textarea className="cv-input" rows={2} value={draft.text}
                      aria-label={tr("第 {0} 条识别文字", draft.index)}
                      onChange={(event) => edit(draft.index, { text: event.target.value })}
                      disabled={readOnly} />
                    <button className="cv-btn cv-btn--ghost cv-btn--sm" type="button"
                      onClick={() => setReviewedDrafts(drafts.filter((item) => item.index !== draft.index))}
                      disabled={readOnly}>{tr("移除这条")}</button>
                  </div>
                ))}
              </div>
              <div className="cv-asr__word-highlight">
                <label>
                  <input type="checkbox" aria-label={tr("逐词高亮")}
                    checked={wordHighlightEnabled}
                    onChange={(event) => setWordHighlightEnabled(event.target.checked)}
                    disabled={readOnly || wordTimeCount === 0} />{tr("逐词高亮")}</label>
                <input type="color" className="cv-input" aria-label={tr("逐词高亮颜色")}
                  value={wordHighlightColor}
                  onChange={(event) => setWordHighlightColor(event.target.value)}
                  disabled={readOnly || !wordHighlightEnabled || wordTimeCount === 0} />
                <span className="cv-hint">
                  {wordTimeCount
                    ? tr("这批结果有 {0} 个词级时间点；改文字会清除对应时间点，改时码会同步缩放。", wordTimeCount)
                    : tr("本批识别结果没有可用的词级时间点。")}
                </span>
              </div>
              <button className="cv-btn cv-btn--primary" type="button" onClick={() => void apply()}
                disabled={busy || readOnly}>{tr("添加 ")}{drafts.length}{tr(" 条到工程")}</button>
            </>
          ) : <p className="cv-hint">{tr("未识别到可用语音，可检查原声或选择其它模型后重试。")}</p>}
        </div>
      ) : null}
    </section>
  );
}
