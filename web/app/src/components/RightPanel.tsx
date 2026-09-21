/** 右侧上下文面板：选中片段 → 片段属性 + 转场 + 变速；选中轨道 → 轨道属性；
 *  无选中 → 工程信息 + 字幕。用 tab 切换。J01 新增「资源库」「效果条」。 */

import { useState } from "react";
import {
  SlidersHorizontal,
  ArrowRightLeft,
  Captions,
  Gauge,
  Library,
  ListOrdered,
  AudioLines,
} from "lucide-react";
import { useEditor } from "../store/editor";
import { Inspector } from "./Inspector";
import { TransitionPanel } from "./TransitionPanel";
import { CaptionPanel } from "./CaptionPanel";
import { SpeedPanel } from "./SpeedPanel";
import { ResourcePanel } from "./ResourcePanel";
import { EffectStackPanel } from "./EffectStackPanel";
import { AudioAnalyzePanel } from "./AudioAnalyzePanel";

type Tab = "inspect" | "transition" | "caption" | "speed" | "resource" | "stack" | "audio";

export function RightPanel() {
  const { state } = useEditor();
  const [tab, setTab] = useState<Tab>("inspect");
  const hasClip = !!state.selection?.clipId;
  const hasTrack = !!state.selection?.trackId && !state.selection?.clipId;

  const tabs: { id: Tab; label: string; icon: React.ReactNode; always?: boolean }[] = [
    { id: "inspect", label: hasClip ? "片段属性" : hasTrack ? "轨道属性" : "工程信息", icon: <SlidersHorizontal size={13} /> },
    { id: "speed", label: "变速", icon: <Gauge size={13} /> },
    { id: "transition", label: "转场", icon: <ArrowRightLeft size={13} /> },
    { id: "caption", label: "字幕", icon: <Captions size={13} /> },
    { id: "resource", label: "资源库", icon: <Library size={13} />, always: true },
    { id: "stack", label: "效果条", icon: <ListOrdered size={13} />, always: true },
    { id: "audio", label: "音频分析", icon: <AudioLines size={13} />, always: true },
  ];

  return (
    <div className="zone-right__inner">
      <div className="right-tabs" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`right-tabs__btn ${tab === t.id ? "right-tabs__btn--active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>
      <div className="zone-right__scroll">
        {tab === "inspect" ? <Inspector /> : null}
        {tab === "speed" ? <SpeedPanel /> : null}
        {tab === "transition" ? <TransitionPanel /> : null}
        {tab === "caption" ? <CaptionPanel /> : null}
        {tab === "resource" ? <ResourcePanel active /> : null}
        {tab === "stack" ? <EffectStackPanel active /> : null}
        {tab === "audio" ? <AudioAnalyzePanel /> : null}
      </div>
    </div>
  );
}