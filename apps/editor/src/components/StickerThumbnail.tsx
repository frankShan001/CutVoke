import { useRef, useState } from "react";
import { tr, useLocale } from "../i18n";
import { assetMediaUrl, stickerPreviewUrl, type BuiltinSticker } from "../lib/mediaApi";

export function StickerThumbnail({ sticker }: { sticker: BuiltinSticker }) {
  useLocale();
  const video = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const stop = () => { video.current?.pause(); setPlaying(false); };
  return <button type="button" className="sticker-thumbnail" aria-label={tr("预览贴纸 {0}", sticker.name)}
    onMouseEnter={() => { if (!failed) void video.current?.play().catch(() => {}); }} onMouseLeave={stop}
    onFocus={() => { if (!failed) void video.current?.play().catch(() => {}); }} onBlur={stop}
    onClick={() => { if (playing) stop(); else if (!failed) void video.current?.play().catch(() => {}); }}>
    {imageFailed ? <span>{tr("缩略图暂不可用")}</span> : <img src={assetMediaUrl(sticker.assetId)} alt={sticker.name} loading="lazy" onError={() => setImageFailed(true)} />}
    {sticker.previewAvailable && !failed && <video ref={video} src={stickerPreviewUrl(sticker.stickerId)}
      className={playing ? "sticker-thumbnail__motion sticker-thumbnail__motion--playing" : "sticker-thumbnail__motion"}
      muted loop playsInline preload="none" aria-hidden="true" onPlaying={() => setPlaying(true)}
      onPause={() => setPlaying(false)} onError={() => { setFailed(true); setPlaying(false); }} />}
  </button>;
}
