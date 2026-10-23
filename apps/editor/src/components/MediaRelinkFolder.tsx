import { useEffect, useRef, useState } from "react";
import { tr } from "../i18n";
import { relinkAsset, scanRelinkFiles, type RelinkMatch } from "../lib/mediaApi";
import { hydrateAssets } from "../lib/assetStore";

function CandidatePreview({ file }: { file: File }) {
  const [url, setUrl] = useState("");
  const [metadata, setMetadata] = useState("");
  useEffect(() => {
    const source = URL.createObjectURL(file); setUrl(source); setMetadata("");
    return () => URL.revokeObjectURL(source);
  }, [file]);
  const describe = (element: HTMLMediaElement) => {
    const video = element as HTMLVideoElement;
    setMetadata(tr("候选规格：{0}×{1}，{2} 秒", video.videoWidth || 0, video.videoHeight || 0,
      Number.isFinite(element.duration) ? element.duration.toFixed(2) : tr("未知")));
  };
  return <details><summary>{tr("预览并核对候选文件")}</summary>
    {file.type.startsWith("image/") ? <img alt={tr("候选文件预览")} src={url} style={{ maxWidth: "100%" }}
      onLoad={event => setMetadata(tr("候选画面：{0}×{1}", event.currentTarget.naturalWidth, event.currentTarget.naturalHeight))} />
      : file.type.startsWith("audio/") ? <audio src={url} controls preload="metadata" onLoadedMetadata={event => describe(event.currentTarget)} style={{ width: "100%" }} />
        : <video src={url} controls preload="metadata" onLoadedMetadata={event => describe(event.currentTarget)} style={{ maxWidth: "100%" }} />}
    <small>{metadata}</small>
  </details>;
}

/** Directory handles remain local; only manifest metadata and confirmed files leave the browser. */
export function MediaRelinkFolder({ disabled, onRestored }: { disabled: boolean; onRestored: () => void }) {
  const picker = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [files, setFiles] = useState<File[]>([]), [matches, setMatches] = useState<RelinkMatch[]>([]);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const [progress, setProgress] = useState({ name: "", loaded: 0, total: 0 });
  const [errors, setErrors] = useState<Record<string, string>>({});
  useEffect(() => () => abort.current?.abort(), []);
  const scan = async (list: FileList | null) => {
    if (!list?.length || disabled) return;
    setBusy(true); setMessage(tr("正在扫描文件夹清单…")); setErrors({});
    try {
      const nextFiles = Array.from(list);
      const results = await scanRelinkFiles(nextFiles);
      setFiles(nextFiles); setMatches(results);
      setSelected(Object.fromEntries(results.filter(item => item.status === "matched").map(item => [item.assetId, item.candidates[0].fileId])));
      setConfirmed(Object.fromEntries(results.map(item => [item.assetId, item.status === "matched"])));
      setMessage(tr("扫描 {0} 个文件，找到 {1} 个缺失素材待核对", nextFiles.length, results.length));
    } catch (error) { setMessage(String(error)); }
    finally { setBusy(false); if (picker.current) picker.current.value = ""; }
  };
  const eligible = matches.filter(item => confirmed[item.assetId] && selected[item.assetId] !== undefined &&
    item.candidates.some(candidate => candidate.fileId === selected[item.assetId]));
  const restore = async () => {
    if (disabled || busy || !eligible.length) return;
    const controller = new AbortController(); abort.current = controller;
    setBusy(true); setErrors({});
    let restored = 0, failed = 0;
    try {
      for (const item of eligible) {
        if (controller.signal.aborted) break;
        const file = files[Number(selected[item.assetId])];
        setProgress({ name: file.webkitRelativePath || file.name, loaded: 0, total: file.size });
        const result = await relinkAsset(item.assetId, file, { verifyIdentity: true,
          confirmLegacy: item.status === "unverified", signal: controller.signal,
          onProgress: (loaded, total) => setProgress({ name: file.webkitRelativePath || file.name, loaded, total }) });
        if (controller.signal.aborted) break;
        if (result.kind === "error") {
          failed++; setErrors(previous => ({ ...previous, [item.assetId]: result.message }));
        } else {
          restored++;
          hydrateAssets([{ ...result.data, available: true }]);
          setConfirmed(previous => ({ ...previous, [item.assetId]: false }));
          window.dispatchEvent(new CustomEvent("cutvoke:asset-restored", { detail: { assetId: result.data.assetId, path: result.data.path } }));
          onRestored();
        }
      }
      setMessage(controller.signal.aborted ? tr("恢复已停止，尚未处理的素材保持缺失") : tr("恢复结束：成功 {0}，失败 {1}", restored, failed));
    } finally { setBusy(false); abort.current = null; setProgress({ name: "", loaded: 0, total: 0 }); }
  };
  return <details className="media-folder-relink" style={{ margin: "8px 0", overflowWrap: "anywhere" }}>
    <summary>{tr("从文件夹批量重新链接")}</summary>
    <p>{tr("先扫描候选并核对，确认后才恢复。多个同名候选必须手动选择；有原始指纹时严格校验内容。")}</p>
    <button type="button" className="cv-btn cv-btn--secondary" disabled={disabled || busy} onClick={() => picker.current?.click()}>{tr("选择素材文件夹并扫描")}</button>
    <input ref={picker} type="file" multiple {...{ webkitdirectory: "", directory: "" }} style={{ display: "none" }}
      aria-label={tr("选择批量重连文件夹")} onChange={event => void scan(event.target.files)} />
    {message ? <p role="status">{message}</p> : null}
    {matches.map(item => {
      const identifier = selected[item.assetId];
      const file = identifier === undefined || identifier === "" ? undefined : files[Number(identifier)];
      return <div key={item.assetId} style={{ borderTop: "1px solid var(--border)", padding: "8px 0" }}>
        <strong>{item.name}</strong>
        <div>{tr("原素材：{0} 字节，{1}×{2}，{3} 秒", item.size, item.expected.width || 0,
          item.expected.height || 0, item.expected.duration ?? tr("未知"))}</div>
        <small>{item.expected.relativePath || item.name}</small>
        {item.status === "unverified" ? <p>{tr("旧素材没有原始指纹：请预览并逐项人工确认；恢复时仍校验大小、类型、时长与画面规格。")}</p> : null}
        {item.candidates.length > 1 ? <p>{tr("候选有歧义，请明确选择文件")}</p> : null}
        {!item.candidates.length ? <p>{tr("未找到大小和路径相符的候选")}</p> : <>
          <select aria-label={tr("为 {0} 选择候选文件", item.name)} disabled={disabled || busy}
            value={selected[item.assetId] ?? ""} style={{ width: "100%" }}
            onChange={event => { setSelected(previous => ({ ...previous, [item.assetId]: event.target.value }));
              setConfirmed(previous => ({ ...previous, [item.assetId]: false })); }}>
            <option value="">{tr("请选择候选文件")}</option>
            {item.candidates.map(candidate => <option key={candidate.fileId} value={candidate.fileId}>{candidate.relativePath}</option>)}
          </select>
          {file ? <CandidatePreview file={file} /> : null}
          <label><input type="checkbox" checked={Boolean(confirmed[item.assetId])} disabled={disabled || busy || !file}
            onChange={event => setConfirmed(previous => ({ ...previous, [item.assetId]: event.target.checked }))} />
            {item.status === "unverified" ? tr("已人工核对该候选，确认恢复") : tr("恢复此素材")}</label>
        </>}
        {errors[item.assetId] ? <p role="alert">{errors[item.assetId]}</p> : null}
      </div>;
    })}
    {matches.length ? <button type="button" className="cv-btn cv-btn--primary" disabled={disabled || busy || !eligible.length}
      onClick={() => void restore()}>{tr("恢复已确认的 {0} 个素材", eligible.length)}</button> : null}
    {busy && progress.name ? <div role="status">
      <span>{tr("当前文件：{0}", progress.name)}</span>
      <span>{tr("已传 {0} / {1} MiB", (progress.loaded / 1048576).toFixed(1), (progress.total / 1048576).toFixed(1))}</span>
      <progress value={progress.loaded} max={progress.total || 1} />
      <button type="button" className="cv-btn cv-btn--secondary" onClick={() => abort.current?.abort()}>{tr("取消当前及后续上传")}</button>
    </div> : null}
  </details>;
}
