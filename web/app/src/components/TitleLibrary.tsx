/** Real rendered title presets; subtitle objects remain a separate workspace. */

import { useEffect, useMemo, useRef, useState } from "react";
import { useEditor } from "../store/editor";
import { getLatestState, runCommand } from "../store/actions";
import { insertTitleAutoTrack } from "../store/clipEdit";
import { builtinPresetMediaUrl, listBuiltinPresets, type BuiltinPresetSummary } from "../lib/effects";
import { rationalToSecs } from "../lib/rational";

export function TitleLibrary({ query }: { query: string }) {
  const { state, dispatch } = useEditor();
  const [content, setContent] = useState("在这里输入标题");
  const [category, setCategory] = useState("");
  const [presets, setPresets] = useState<BuiltinPresetSummary[]>([]);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [adding, setAdding] = useState<string | null>(null);
  const [preview, setPreview] = useState<BuiltinPresetSummary | null>(null);
  const previewOpener = useRef<HTMLButtonElement | null>(null);
  const previewClose = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setLoadError("");
    listBuiltinPresets().then((catalog) => {
      if (!live) return;
      setPresets(catalog.presets.filter((item) => item.family === "text" && item.status !== "retired"));
      setLoadError("");
    }).catch((error: unknown) => {
      if (live) setLoadError(error instanceof Error ? error.message : "文字预设目录读取失败");
    }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [loadAttempt]);

  useEffect(() => {
    if (!preview) return;
    previewClose.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPreview(null);
        requestAnimationFrame(() => previewOpener.current?.focus());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview]);

  const videoEnd = Math.max(0, ...((state.project?.sequence.tracks || [])
    .filter((track) => track.kind === "video" && track.role !== "sticker")
    .flatMap((track) => track.clips.map((clip) => rationalToSecs(clip.timelineEnd)))));
  const frame = state.project?.sequence.fps;
  const frameDuration = frame ? Number(frame.den) / Number(frame.num) : 1 / 30;
  const canAdd = !!state.currentId && !state.editLock && content.trim().length > 0 &&
    videoEnd - state.playhead >= frameDuration;
  const selectedTitle = useMemo(() => {
    const selectedId = state.selection?.clipId;
    if (!selectedId) return null;
    const track = state.project?.sequence.tracks.find((item) =>
      item.kind === "text" && item.clips.some((clip) => clip.id === selectedId));
    return track && !track.locked ? selectedId : null;
  }, [state.project, state.selection?.clipId]);
  const categories = [...new Set(presets.map((item) => item.subcategory))];
  const needle = query.trim().toLocaleLowerCase();
  const visible = presets.filter((preset) =>
    (!category || preset.subcategory === category) &&
    (!needle || [preset.name, preset.subcategory, preset.presetId]
      .some((word) => word.toLocaleLowerCase().includes(needle))));

  const add = async (preset: BuiltinPresetSummary) => {
    if (!canAdd || adding || !preset.mediaAvailable) return;
    setAdding(preset.presetId);
    try {
      const latest = getLatestState() || state;
      const result = await insertTitleAutoTrack(dispatch, latest, {
        presetId: preset.presetId,
        text: { content: content.trim() },
        timelineStartSecs: latest.playhead,
        durationSecs: Math.min(preset.defaultDuration || 3, videoEnd - latest.playhead),
      });
      if (result.ok) {
        const changes = result.command?.changedEntities || [];
        const trackId = changes.find((item) => item.type === "track" && item.change === "created")?.id;
        const clipId = changes.find((item) => item.type === "clip" && item.change === "created")?.id;
        if (trackId && clipId) dispatch({ type: "SELECTION_SET", selection: { trackId, clipId } });
        dispatch({ type: "STATUS_SET", severity: "ok", text: `已添加可编辑标题「${preset.name}」` });
      }
    } finally { setAdding(null); }
  };

  const replace = async (preset: BuiltinPresetSummary) => {
    if (!selectedTitle || state.editLock || adding || !preset.mediaAvailable) return;
    setAdding(preset.presetId);
    try {
      const latest = getLatestState() || state;
      const result = await runCommand(dispatch, latest, "builtinPreset.apply", {
        clipId: selectedTitle, presetId: preset.presetId,
      });
      if (result.ok) dispatch({ type: "STATUS_SET", severity: "ok", text: `已更换标题样式「${preset.name}」` });
    } finally { setAdding(null); }
  };

  return <section className="title-library" aria-label="文字标题库">
    <strong>普通文字、花字与文字动画 · 真实样片</strong>
    <p className="cv-hint">标题是独立文字轨片段，可继续修改内容与样式；字幕在右侧字幕工作台编辑。</p>
    <label className="title-library__input">标题内容
      <textarea className="cv-input" value={content} onChange={(event) => setContent(event.target.value)}
        rows={2} maxLength={180} aria-label="标题内容" />
    </label>
    <div className="media-filter__kinds" role="group" aria-label="文字类型">
      {["", ...categories].map((item) => <button key={item || "all"} type="button"
        className={`media-filter__kind ${category === item ? "media-filter__kind--active" : ""}`}
        aria-pressed={category === item} onClick={() => setCategory(item)}>{item || "全部"}</button>)}
    </div>
    {!canAdd ? <p className="cv-hint" role="status">先添加视频或背景，并将播放头放在画面范围内；标题内容不能留空。</p> : null}
    {loading ? <p className="cv-hint" role="status">正在读取文字预设…</p> : null}
    {loadError ? <p className="resource-pack-manager__error" role="alert">
      <span>文字预设目录读取失败：{loadError}</span>
      <button type="button" className="cv-button cv-button--small" disabled={loading}
        onClick={() => setLoadAttempt((attempt) => attempt + 1)}>重试文字预设</button>
    </p> : null}
    <div className="title-library__grid">
      {visible.map((preset) => {
        const mediaReady = preset.downloadState === "bundled" && preset.mediaAvailable;
        return <article key={preset.presetId} className="title-library__card">
          <button type="button" className="title-library__preview" disabled={!mediaReady}
            onClick={(event) => { previewOpener.current = event.currentTarget; setPreview(preset); }}
            aria-label={`播放${preset.name}文字样片`}>
            {mediaReady ? <img loading="lazy" alt="" src={builtinPresetMediaUrl(preset.presetId, "cover")} /> : "样片不可用"}
          </button>
          <strong>{preset.name}</strong>
          <small>{preset.subcategory} · {preset.qualified ? "已验收" : "待完整验收"}</small>
          <div className="title-library__actions">
            <button type="button" disabled={!canAdd || !!adding || !mediaReady}
              onClick={() => void add(preset)} aria-label={`添加标题 ${preset.name}`}>添加</button>
            <button type="button" disabled={!selectedTitle || !!state.editLock || !!adding || !mediaReady}
              onClick={() => void replace(preset)} aria-label={`替换所选标题为 ${preset.name}`}>替换</button>
          </div>
        </article>;
      })}
    </div>
    {!loading && !loadError && visible.length === 0 ? <p className="cv-empty">没有匹配的标题样式。</p> : null}
    {preview ? <div className="resource-sample-overlay" onMouseDown={(event) => {
      if (event.target === event.currentTarget) {
        setPreview(null);
        requestAnimationFrame(() => previewOpener.current?.focus());
      }
    }}>
      <section className="resource-sample-dialog" role="dialog" aria-modal="true"
        aria-label={`${preview.name}文字样片`} onKeyDown={(event) => {
          if (event.key !== "Tab") return;
          const video = event.currentTarget.querySelector("video");
          if (event.shiftKey && document.activeElement === previewClose.current) {
            event.preventDefault(); video?.focus();
          } else if (!event.shiftKey && document.activeElement === video) {
            event.preventDefault(); previewClose.current?.focus();
          }
        }}>
        <div className="resource-sample-dialog__head"><strong>{preview.name}</strong>
          <button ref={previewClose} type="button" onClick={() => {
            setPreview(null); requestAnimationFrame(() => previewOpener.current?.focus());
          }} aria-label="关闭文字样片">关闭</button></div>
        <video controls autoPlay muted loop playsInline tabIndex={0} preload="metadata"
          poster={builtinPresetMediaUrl(preview.presetId, "cover")}
          src={builtinPresetMediaUrl(preview.presetId, "preview")} />
      </section>
    </div> : null}
  </section>;
}
