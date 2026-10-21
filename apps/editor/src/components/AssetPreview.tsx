import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { tr, useLocale } from "../i18n";

export interface PreviewAsset { name: string; url: string; kind: "video" | "audio" | "image" | "unknown" }

/** Native modal owns focus; removing the player also stops its audio/network work. */
export function AssetPreview({ asset, onClose }: { asset: PreviewAsset; onClose(): void }) {
  useLocale();
  const dialog = useRef<HTMLDialogElement>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const element = dialog.current;
    window.dispatchEvent(new Event("cutvoke:pause-play"));
    element?.showModal();
    return () => {
      for (const player of element?.querySelectorAll<HTMLMediaElement>("audio,video") || []) {
        player.pause(); player.removeAttribute("src"); player.load();
      }
      element?.close();
    };
  }, []);
  return <dialog ref={dialog} role="dialog" className="asset-preview" aria-labelledby="asset-preview-title"
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) {
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
    } }}>
    <header><h2 id="asset-preview-title">{asset.name}</h2><button type="button" autoFocus onClick={onClose} aria-label={tr("关闭素材预览")}><X size={20} /></button></header>
    <div className="asset-preview__stage" key={attempt}>
      {asset.kind === "image" ? <img src={asset.url} alt={asset.name} onError={() => setFailed(true)} />
        : asset.kind === "audio" ? <audio src={asset.url} controls autoPlay preload="metadata" onError={() => setFailed(true)} />
          : <video src={asset.url} controls autoPlay playsInline preload="metadata" onError={() => setFailed(true)} />}
    </div>
    {failed && <div role="alert"><p>{tr("素材无法预览，请检查文件是否仍可访问或浏览器是否支持此格式。")}</p>
      <button type="button" onClick={() => { setFailed(false); setAttempt(value => value + 1); }}>{tr("重试")}</button></div>}
  </dialog>;
}
