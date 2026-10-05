import { useEffect, useState } from "react";
import { Check, Loader2, Scissors, X } from "lucide-react";
import {
  applyPersonCutoutJob,
  cancelPersonCutoutJob,
  getPersonCutoutJob,
  getPersonCutoutStatus,
  personCutoutPreviewUrl,
  startPersonCutoutJob,
  type PersonCutoutJob,
  type PersonCutoutStatus,
} from "../../lib/api";
import { Button } from "../ui";
import { showError, useEditor } from "../../store/editor";
import { refreshProject } from "../../store/actions";

const JOB_STORAGE_PREFIX = "cutvoke.person-cutout-job:";
const RUNNING_STATES = new Set<PersonCutoutJob["status"]>(["queued", "running", "applying"]);

export function PersonCutoutSection({
  active,
  projectId,
  clipId,
  readOnly,
}: {
  active: boolean;
  projectId: string | null;
  clipId: string | null;
  readOnly: boolean;
}) {
  const { state, dispatch } = useEditor();
  const [availability, setAvailability] = useState<PersonCutoutStatus | null>(null);
  const [job, setJob] = useState<PersonCutoutJob | null>(null);
  const [edgeSoftness, setEdgeSoftness] = useState(1.2);
  const [selectionMode, setSelectionMode] = useState<"all" | "selected">("all");
  const [trackingMode, setTrackingMode] = useState<"semantic" | "magic_touch" | "sam2">("semantic");
  const [selectionPoints, setSelectionPoints] = useState<
    { x: number; y: number; label: 0 | 1 }[]
  >([]);
  const [promptAtSeconds, setPromptAtSeconds] = useState("0");
  const [promptFrames, setPromptFrames] = useState<{
    atSeconds: number;
    points: { x: number; y: number; label: 0 | 1 }[];
  }[]>([]);
  const selectionPoint = selectionPoints.find((point) => point.label === 1) ?? null;
  const [previewFailed, setPreviewFailed] = useState(false);
  const [busy, setBusy] = useState<"start" | "cancel" | "apply" | null>(null);
  const [statusError, setStatusError] = useState("");

  useEffect(() => {
    let disposed = false;
    setAvailability(null);
    setStatusError("");
    if (!active || !projectId) return () => { disposed = true; };
    void getPersonCutoutStatus().then((result) => {
      if (!disposed) setAvailability(result);
    }).catch((error: unknown) => {
      if (!disposed) {
        setAvailability({ installed: false, modelReady: false, interactiveModelReady: false, ready: false, message: "" });
        setStatusError(error instanceof Error ? error.message : String(error));
      }
    });
    return () => { disposed = true; };
  }, [active, projectId]);

  useEffect(() => {
    let disposed = false;
    setJob(null);
    if (!projectId) return () => { disposed = true; };
    const key = `${JOB_STORAGE_PREFIX}${projectId}`;
    const savedId = window.sessionStorage.getItem(key);
    if (!savedId) return () => { disposed = true; };
    void getPersonCutoutJob(savedId).then((result) => {
      if (!disposed && result.projectId === projectId) setJob(result);
    }).catch(() => {
      window.sessionStorage.removeItem(key);
    });
    return () => { disposed = true; };
  }, [projectId]);

  useEffect(() => {
    setSelectionMode("all");
    setTrackingMode("semantic");
    setSelectionPoints([]);
    setPreviewFailed(false);
  }, [projectId, clipId]);

  useEffect(() => {
    if (availability?.sam2Ready && !availability.modelReady) {
      setSelectionMode("selected");
      setTrackingMode("sam2");
    }
  }, [availability?.sam2Ready, availability?.modelReady]);

  useEffect(() => {
    if (!job || !RUNNING_STATES.has(job.status)) return;
    let disposed = false;
    let polling = false;
    const timer = window.setInterval(() => {
      if (polling) return;
      polling = true;
      void getPersonCutoutJob(job.jobId).then((latest) => {
        if (!disposed) setJob(latest);
      }).catch((error: unknown) => {
        if (!disposed) setStatusError(error instanceof Error ? error.message : String(error));
      }).finally(() => { polling = false; });
    }, 900);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [job?.jobId, job?.status]);

  const saveJob = (value: PersonCutoutJob | null) => {
    setJob(value);
    if (!projectId) return;
    const key = `${JOB_STORAGE_PREFIX}${projectId}`;
    if (value) window.sessionStorage.setItem(key, value.jobId);
    else window.sessionStorage.removeItem(key);
  };

  const selectedTrack = state.project?.sequence.tracks.find((track) =>
    track.clips.some((clip) => clip.id === clipId));
  const selectedClip = selectedTrack?.clips.find((clip) => clip.id === clipId);
  const selectedIsVideo = selectedTrack?.kind === "video" && selectedTrack.role !== "sticker";
  const sourceStartSeconds = selectedClip
    ? Number(selectedClip.sourceStart.num) / Math.max(1, Number(selectedClip.sourceStart.den))
    : 0;
  const selectedDurationSeconds = selectedClip
    ? (Number(selectedClip.timelineEnd.num) / Number(selectedClip.timelineEnd.den)) -
      (Number(selectedClip.timelineStart.num) / Number(selectedClip.timelineStart.den))
    : 0;
  const selectedSpeed = selectedClip?.speed
    ? Math.abs(Number(selectedClip.speed.num) / Number(selectedClip.speed.den)) : 1;
  const sourceDurationSeconds = selectedClip?.speedCurve
    ? Number(selectedClip.speedCurve.sourceDuration.num) /
      Number(selectedClip.speedCurve.sourceDuration.den)
    : selectedDurationSeconds * selectedSpeed;
  const sourceEndSeconds = sourceStartSeconds + sourceDurationSeconds;
  const promptTime = Number(promptAtSeconds);
  const promptTimeValid = Number.isFinite(promptTime) &&
    promptTime >= sourceStartSeconds && promptTime < sourceEndSeconds;
  const hasSelectionPrompt = !!selectionPoint || promptFrames.some((frame) =>
    frame.points.some((point) => point.label === 1));
  const jobMatchesSelection = !!job && !!clipId && job.clipId === clipId;
  const jobCanCancel = !!job && RUNNING_STATES.has(job.status);
  const jobCanApply = !!job && job.status === "completed" && jobMatchesSelection && !readOnly;

  useEffect(() => {
    setPromptAtSeconds(String(sourceStartSeconds));
    setPromptFrames([]);
    setSelectionPoints([]);
    setPreviewFailed(false);
  }, [projectId, clipId, sourceStartSeconds]);

  const handleStart = async () => {
    if (!projectId || !clipId) return;
    const frames = [...promptFrames];
    if (trackingMode === "sam2" && selectionPoints.length > 0) {
      if (!promptTimeValid) {
        setStatusError("提示帧时间必须位于所选片段的源素材范围内。");
        return;
      }
      const atSeconds = Number(promptTime.toFixed(3));
      const existing = frames.find((frame) => Math.abs(frame.atSeconds - atSeconds) < 0.001);
      const points = existing ? [...existing.points, ...selectionPoints] : selectionPoints;
      if (points.length > 8) {
        setStatusError("同一帧最多保存 8 个正向或排除点。");
        return;
      }
      if (existing) {
        const index = frames.findIndex((frame) => frame === existing);
        frames[index] = { ...existing, points };
      }
      else frames.push({ atSeconds, points: [...selectionPoints] });
    } else if (trackingMode !== "sam2" && selectionPoints.length > 0) {
      frames.push({ atSeconds: sourceStartSeconds, points: [...selectionPoints] });
    }
    frames.sort((a, b) => a.atSeconds - b.atSeconds);
    if (selectionMode === "selected" &&
        (!frames.length || !frames.some((frame) => frame.points.some((point) => point.label === 1)))) {
      setStatusError("至少在一个提示帧上点选目标人物。");
      return;
    }
    if (frames.length > 16) {
      setStatusError("SAM2 最多支持 16 个提示帧。");
      return;
    }
    const primaryPromptPoints = frames[0]?.points ?? selectionPoints;
    const primaryPoint = primaryPromptPoints.find((point) => point.label === 1) ?? null;
    setBusy("start");
    setStatusError("");
    try {
      const selected = await startPersonCutoutJob(projectId, {
        clipId,
        edgeSoftness,
        selectionMode,
        trackingMode,
        selectionPoint: primaryPoint ? { x: primaryPoint.x, y: primaryPoint.y } : null,
        selectionPoints: trackingMode === "sam2"
          ? primaryPromptPoints.map(({ x, y, label }) => ({ x, y, label }))
          : primaryPoint ? [{ ...primaryPoint, label: 1 as const }] : [],
        selectionAtSeconds: trackingMode === "sam2" && frames.length
          ? frames[0].atSeconds : sourceStartSeconds,
        selectionPrompts: selectionMode === "selected"
          ? frames.map((frame) => ({
            atSeconds: frame.atSeconds,
            points: frame.points.map(({ x, y, label }) => ({ x, y, label })),
          }))
          : undefined,
      });
      saveJob(selected);
    } catch (error) {
      showError(dispatch, error);
    } finally {
      setBusy(null);
    }
  };

  const handleCancel = async () => {
    if (!job) return;
    setBusy("cancel");
    try {
      saveJob(await cancelPersonCutoutJob(job.jobId));
    } catch (error) {
      showError(dispatch, error);
    } finally {
      setBusy(null);
    }
  };

  const handleApply = async () => {
    if (!projectId || !job || !jobMatchesSelection || readOnly) return;
    setBusy("apply");
    try {
      const applied = await applyPersonCutoutJob(projectId, job.jobId);
      await refreshProject(dispatch, projectId);
      dispatch({ type: "UNDO_SET", blocked: false });
      saveJob(applied.job);
      dispatch({ type: "STATUS_SET", severity: "ok", text: "人物抠像已应用；可撤销恢复原片" });
    } catch (error) {
      showError(dispatch, error);
      try {
        saveJob(await getPersonCutoutJob(job.jobId));
      } catch {
        // Keep the last visible job state if a second status request also fails.
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="person-cutout" aria-labelledby="person-cutout-title">
      <div className="person-cutout__heading">
        <span className="person-cutout__icon"><Scissors size={14} /></span>
        <div>
          <h3 id="person-cutout-title">人物抠像</h3>
          <p>本机生成透明视频，可叠加到其他画面</p>
        </div>
      </div>

      {statusError ? <p className="person-cutout__message person-cutout__message--error" role="alert">{statusError}</p> : null}
      {!availability?.ready ? (
        <div className="person-cutout__setup" role="status">
          <p>{availability?.message || "正在检查本机抠像组件…"}</p>
          {availability && !availability.installed ? <code>pip install "cutvoke[person]"</code> : null}
          {availability?.installed && !availability.modelReady
            ? <code>uv run cutvoke-person-model</code> : null}
          {!availability?.sam2Ready
            ? <code>uv run cutvoke-person-model --sam2</code> : null}
        </div>
      ) : (
        <>
          <p className="person-cutout__message person-cutout__message--success">
            {availability.message}
          </p>
          <label className="person-cutout__softness">
            <span>边缘柔化 <strong>{edgeSoftness.toFixed(1)} px</strong></span>
            <input aria-label="人物抠像边缘柔化" type="range" min="0" max="8" step="0.2"
              value={edgeSoftness} disabled={!!busy || jobCanCancel}
              onChange={(event) => setEdgeSoftness(Number(event.target.value))} />
          </label>
          <fieldset className="person-cutout__selection" disabled={!!busy || jobCanCancel || readOnly}>
            <legend>人物范围</legend>
            <label>
              <input type="radio" name="person-cutout-selection" value="all"
                checked={selectionMode === "all"}
                disabled={!availability?.modelReady}
                onChange={() => { setSelectionMode("all"); setTrackingMode("semantic"); setSelectionPoints([]); setPromptFrames([]); }} />
              保留所有可见人物
            </label>
            <label>
              <input type="radio" name="person-cutout-selection" value="selected"
                checked={selectionMode === "selected"}
                disabled={!availability?.modelReady && !availability?.sam2Ready}
                onChange={() => {
                  setSelectionMode("selected");
                  setSelectionPoints([]);
                  setPromptFrames([]);
                  setTrackingMode(availability?.sam2Ready && !availability.modelReady ? "sam2" : "semantic");
                  setPreviewFailed(false);
                }} />
              只保留我点选的人物
            </label>
          </fieldset>
          {selectionMode === "selected" && projectId && clipId ? (
            <div className="person-cutout__picker">
              <p>点选人物添加提示点；SAM2 模式支持在后续帧追加纠正点，按住 Shift 点其他人可添加排除点。</p>
              <label className="person-cutout__tracking">
                <span>跟踪方式</span>
                <select aria-label="人物抠像跟踪方式" value={trackingMode}
                  disabled={!!busy || jobCanCancel || readOnly}
                  onChange={(event) => {
                    setTrackingMode(event.target.value as "semantic" | "magic_touch" | "sam2");
                    setPromptFrames([]);
                    setSelectionPoints([]);
                  }}>
                  <option value="semantic" disabled={!availability?.modelReady}>轮廓连续跟踪</option>
                  <option value="magic_touch" disabled={!availability?.interactiveModelReady}>
                    MagicTouch 点提示逐帧重分割
                  </option>
                  <option value="sam2" disabled={!availability?.sam2Ready}>SAM2 视频身份跟踪</option>
                </select>
              </label>
              {!availability?.interactiveModelReady
                ? <p className="person-cutout__message">准备可选模型：<code>uv run cutvoke-person-model --interactive</code></p>
                : null}
              {!availability?.sam2Ready
                ? <p className="person-cutout__message">安装更强的视频身份跟踪：<code>uv run cutvoke-person-model --sam2</code></p>
                : null}
              {trackingMode === "sam2" ? (
                <label className="person-cutout__prompt-time">
                  <span>预览源素材时间（秒）</span>
                  <input aria-label="SAM2 提示帧源素材时间" type="number" min={sourceStartSeconds}
                    max={Math.max(sourceStartSeconds, sourceEndSeconds - 0.001)} step="0.1"
                    value={promptAtSeconds} disabled={!!busy || jobCanCancel || readOnly}
                    onChange={(event) => {
                      setPromptAtSeconds(event.target.value);
                      setPreviewFailed(false);
                    }} />
                </label>
              ) : null}
              {previewFailed ? (
                <p className="person-cutout__message person-cutout__message--error" role="alert">
                  无法提取当前源素材时间的预览帧，请检查原视频或先重新链接素材。
                </p>
              ) : (
                <button className="person-cutout__frame" type="button"
                  aria-label="点选要保留的人物"
                  onClick={(event) => {
                    const image = event.currentTarget.querySelector("img");
                    if (!image?.naturalWidth || !image.naturalHeight) return;
                    const rect = image.getBoundingClientRect();
                    const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
                    const drawnWidth = image.naturalWidth * scale;
                    const drawnHeight = image.naturalHeight * scale;
                    const left = rect.left + (rect.width - drawnWidth) / 2;
                    const top = rect.top + (rect.height - drawnHeight) / 2;
                    const x = (event.clientX - left) / drawnWidth;
                    const y = (event.clientY - top) / drawnHeight;
                    if (x < 0 || x > 1 || y < 0 || y > 1) return;
                    const label = trackingMode === "sam2" && event.shiftKey ? 0 : 1;
                    setSelectionPoints((current) => trackingMode === "sam2"
                      ? (current.length >= 8 ? current : [...current, { x, y, label }])
                      : [{ x, y, label: 1 }]);
                  }}>
                  <img src={personCutoutPreviewUrl(projectId, clipId,
                    trackingMode === "sam2" && promptTimeValid ? promptTime : sourceStartSeconds)}
                    alt={trackingMode === "sam2" ? `源素材 ${promptAtSeconds} 秒画面` : "片段源入点画面"}
                    onError={() => setPreviewFailed(true)} />
                  {selectionPoints.map((point, index) => <span
                    key={`${index}-${point.x.toFixed(3)}-${point.y.toFixed(3)}`}
                    className={`person-cutout__target-mark${point.label === 0 ? " person-cutout__target-mark--exclude" : ""}`}
                    style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }} />)}
                </button>
              )}
              {selectionPoints.length > 0 ? <div className="person-cutout__prompt-actions">
                <p className="person-cutout__message">
                  {selectionPoints.filter((point) => point.label === 1).length} 个人物点 · {selectionPoints.filter((point) => point.label === 0).length} 个排除点
                </p>
                <button type="button" className="person-cutout__clear-points"
                  onClick={() => setSelectionPoints([])}>清除点位</button>
              </div> : null}
              {trackingMode === "sam2" ? (
                <>
                  <button type="button" className="person-cutout__save-prompt"
                    disabled={!!busy || jobCanCancel || readOnly || !selectionPoints.length ||
                      !promptTimeValid || (!selectionPoints.some((point) => point.label === 1))}
                    onClick={() => {
                      const atSeconds = Number(promptTime.toFixed(3));
                      const existing = promptFrames.find((frame) =>
                        Math.abs(frame.atSeconds - atSeconds) < 0.001);
                      if (existing && existing.points.length + selectionPoints.length > 8) {
                        setStatusError("同一帧最多保存 8 个正向或排除点。");
                        return;
                      }
                      if (!existing && promptFrames.length >= 16) {
                        setStatusError("SAM2 最多支持 16 个提示帧。");
                        return;
                      }
                      setPromptFrames((current) => {
                        const match = current.find((frame) =>
                          Math.abs(frame.atSeconds - atSeconds) < 0.001);
                        const next = match
                          ? current.map((frame) => frame === match
                            ? { ...frame, points: [...frame.points, ...selectionPoints] } : frame)
                          : [...current, { atSeconds, points: [...selectionPoints] }];
                        return next.sort((a, b) => a.atSeconds - b.atSeconds);
                      });
                      setSelectionPoints([]);
                      setStatusError("");
                    }}>
                    保存此帧提示
                  </button>
                  {promptFrames.length > 0 ? (
                    <div className="person-cutout__saved-prompts" aria-label="已保存的 SAM2 提示帧">
                      {promptFrames.map((frame) => (
                        <div className="person-cutout__saved-prompt" key={frame.atSeconds}>
                          <span>{frame.atSeconds.toFixed(2)} 秒 · {frame.points.length} 点</span>
                          <button type="button" aria-label={`编辑 ${frame.atSeconds.toFixed(2)} 秒提示帧`}
                            disabled={!!busy || jobCanCancel || readOnly}
                            onClick={() => {
                              setPromptAtSeconds(String(frame.atSeconds));
                              setSelectionPoints([...frame.points]);
                              setPromptFrames((current) => current.filter((item) => item !== frame));
                              setPreviewFailed(false);
                            }}>编辑</button>
                          <button type="button" aria-label={`移除 ${frame.atSeconds.toFixed(2)} 秒提示帧`}
                            disabled={!!busy || jobCanCancel || readOnly}
                            onClick={() => setPromptFrames((current) =>
                              current.filter((item) => item !== frame))}>移除</button>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </>
              ) : null}
              {hasSelectionPrompt ? <p className="person-cutout__message">
                {trackingMode === "sam2"
                  ? "SAM2 会从提示帧向前、向后跟踪人物；遮挡后可调整时间并保存纠正提示帧。"
                  : trackingMode === "magic_touch"
                  ? "已选中提示点；MagicTouch 每帧重新分割可见区域，完全遮挡或人物接触时仍可能丢失或合并。"
                  : "已选中画面目标；轮廓交叠或接触时可能丢失或合并。"}
              </p> : <p className="person-cutout__message">请先在画面人物躯干上点一个正向提示点。</p>}
            </div>
          ) : null}
          {job ? (
            <div className="person-cutout__job" role="status" aria-live="polite">
              <div className="person-cutout__phase">
                <span>{job.phase}</span>
                <span>{job.status === "completed" || job.status === "applied" ? <Check size={13} /> : `${job.progress}%`}</span>
              </div>
              <div className="person-cutout__progress" aria-label={`抠像进度 ${job.progress}%`}>
                <span style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} />
              </div>
              {job.error ? <p className="person-cutout__message person-cutout__message--error">{job.error}</p> : null}
              {job.result ? <>
                <p className="person-cutout__meta">
                  {job.result.frameCount} 帧 · {job.result.durationSeconds.toFixed(1)} 秒
                  {job.result.audioPreserved ? " · 已保留原音频" : " · 无源音频"}
                  {job.selectionMode === "selected_person"
                    ? <>
                      {job.trackingMode === "sam2" ? " · SAM2 视频身份跟踪" :
                        job.trackingMode === "magic_touch" ? " · MagicTouch 点提示跟踪" : " · 单人轮廓跟踪"}
                      {typeof job.result.selectedFrames === "number"
                        ? ` · 跟踪 ${job.result.selectedFrames}/${job.result.frameCount} 帧 · 丢失 ${job.result.lostFrames ?? 0} 帧`
                        : ""}
                    </>
                    : " · 全人物"}
                </p>
                {job.selectionMode === "selected_person" && (job.result.lostFrames ?? 0) > 0 ? (
                  <p className="person-cutout__message person-cutout__message--warning" role="alert">
                    跟踪丢失 {job.result.lostFrames} 帧，输出对应区间可能透明断开；建议先检查结果画面，再应用到片段。
                  </p>
                ) : null}
              </> : null}
              {job.status === "completed" && !jobMatchesSelection
                ? <p className="person-cutout__message">选择原片段后即可应用生成结果。</p> : null}
              {job.status === "applied"
                ? <p className="person-cutout__message person-cutout__message--success">已替换片段素材，撤销可恢复原片。</p> : null}
              <div className="person-cutout__actions">
                {jobCanCancel ? (
                  <Button variant="secondary" size="sm" disabled={!!busy} onClick={() => void handleCancel()}>
                    {busy === "cancel" ? <Loader2 size={12} className="cv-spin" /> : <X size={12} />}
                    取消
                  </Button>
                ) : null}
                {jobCanApply ? (
                  <Button variant="primary" size="sm" disabled={!!busy} onClick={() => void handleApply()}>
                    {busy === "apply" ? <Loader2 size={12} className="cv-spin" /> : <Check size={12} />}
                    应用到片段
                  </Button>
                ) : null}
              </div>
            </div>
          ) : null}
          <Button variant="secondary" size="sm" full
            disabled={!selectedIsVideo || readOnly || !!busy || jobCanCancel ||
              (selectionMode === "selected" && !hasSelectionPrompt)}
            onClick={() => void handleStart()}>
            {busy === "start" ? <Loader2 size={12} className="cv-spin" /> : <Scissors size={12} />}
            {job?.status === "failed" || job?.status === "cancelled" ? "重新抠像" :
              selectionMode === "selected" ? "跟踪并抠出所选人物" : "开始抠像"}
          </Button>
          {!selectedIsVideo ? <p className="person-cutout__message">先选中普通视频轨道上的一个片段。</p> : null}
          {readOnly && selectedIsVideo ? <p className="person-cutout__message">当前片段不可编辑，解锁后可应用抠像结果。</p> : null}
        </>
      )}
    </section>
  );
}
