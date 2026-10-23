import { useState } from "react";
import { useEditor } from "../../store/editor";
import { batchClips, copySelectedClips, duplicateSelectedClips, pasteClipsAtPlayhead } from "../../store/timelineClipboard";
import { selectedClipIds } from "../../store/timelineSelection";
import { secsToFrameRatString } from "../timeline/util";
import { tr } from "../../i18n";

export function BatchClipSection() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const ids = selectedClipIds(state);
  const [volume, setVolume] = useState("");
  const [fadeIn, setFadeIn] = useState("");
  const [fadeOut, setFadeOut] = useState("");
  const [offset, setOffset] = useState("0");
  const [trackOffset, setTrackOffset] = useState("0");
  const [busy, setBusy] = useState(false);
  if (ids.length < 2) return null;
  const blocked = busy || !!state.editLock;
  const run = async (action: "audio" | "move" | "remove", values: Record<string,unknown> = {}) => {
    setBusy(true);
    try { await batchClips(dispatch, state, action, values); } finally { setBusy(false); }
  };
  const valid = (value: string, max: number) => value === "" || (Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= max);
  return <section className="cv-section" aria-label={tr("批量片段编辑")}>
    <h3>{tr("已选择 {0} 个片段", ids.length)}</h3>
    <p className="cv-hint">{tr("Ctrl/⌘ 单击增减选择，Shift 单击连续选择；拖动选中片段可整体移动。")}</p>
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        <button className="cv-btn cv-btn--sm" disabled={blocked} onClick={() => void copySelectedClips(dispatch,state)}>{tr("复制到剪贴板")}</button>
        <button className="cv-btn cv-btn--sm" disabled={blocked} onClick={() => void pasteClipsAtPlayhead(dispatch,state)}>{tr("粘贴到播放头")}</button>
        <button className="cv-btn cv-btn--sm" disabled={blocked} onClick={() => void duplicateSelectedClips(dispatch,state)}>{tr("插入副本")}</button>
      </div>
      <label>{tr("批量音量 (%)")}<input className="cv-input" type="number" min="0" max="800" placeholder={tr("保持各自原值")}
        value={volume} onChange={event=>setVolume(event.target.value)} /></label>
      <label>{tr("批量淡入 (s)")}<input className="cv-input" type="number" min="0" step="any" placeholder={tr("保持各自原值")}
        value={fadeIn} onChange={event=>setFadeIn(event.target.value)} /></label>
      <label>{tr("批量淡出 (s)")}<input className="cv-input" type="number" min="0" step="any" placeholder={tr("保持各自原值")}
        value={fadeOut} onChange={event=>setFadeOut(event.target.value)} /></label>
      <button className="cv-btn cv-btn--sm" disabled={blocked || ![volume,fadeIn,fadeOut].some(v=>v!=="")
        || !valid(volume,800) || !valid(fadeIn,86400) || !valid(fadeOut,86400)} onClick={()=>void run("audio",{
          ...(volume!==""?{volume:Number(volume)/100}:{}), ...(fadeIn!==""?{fadeIn:Number(fadeIn)}:{}), ...(fadeOut!==""?{fadeOut:Number(fadeOut)}:{})
        })}>{tr("应用批量音频属性")}</button>
      <label>{tr("整体移动秒数")}<input className="cv-input" type="number" step="any" value={offset} onChange={e=>setOffset(e.target.value)} /></label>
      <label>{tr("整体轨道位移")}<input className="cv-input" type="number" step="1" value={trackOffset} onChange={e=>setTrackOffset(e.target.value)} /></label>
      <button className="cv-btn cv-btn--sm" disabled={blocked || !Number.isFinite(Number(offset)) || !Number.isInteger(Number(trackOffset))}
        onClick={()=>void run("move",{offset:secsToFrameRatString(Number(offset),state.project?.sequence.fps),trackOffset:Number(trackOffset)})}>{tr("应用整体移动")}</button>
      <p className="cv-hint">{tr("移动和删除会包含绑定图层；锁定或位置冲突时整次操作均不执行。")}</p>
      <button className="cv-btn cv-btn--sm" disabled={blocked} onClick={()=>void run("remove")}>{tr("删除所选片段及绑定图层")}</button>
    </div>
  </section>;
}
