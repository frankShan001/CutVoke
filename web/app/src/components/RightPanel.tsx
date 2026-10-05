/** 右侧上下文面板：选中片段 → 片段属性 + 转场 + 变速；选中轨道 → 轨道属性；
 *  无选中 → 工程信息 + 字幕。用 tab 切换。J01 新增「资源库」「效果条」。 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  SlidersHorizontal,
  ArrowRightLeft,
  Gauge,
  ListOrdered,
  AudioLines,
} from "lucide-react";
import { useEditor } from "../store/editor";
import { Inspector } from "./Inspector";
import { TransitionPanel } from "./TransitionPanel";
import { SpeedPanel } from "./SpeedPanel";
import { EffectStackPanel } from "./EffectStackPanel";
import { AudioAnalyzePanel } from "./AudioAnalyzePanel";
import { LOCATE_PREFLIGHT_ISSUE, type LocatePreflightIssueEvent } from "../lib/preflight";

type Tab = "inspect" | "transition" | "speed" | "stack" | "audio";

export function RightPanel() {
  const { state } = useEditor({ subscribeToClock: false });
  const [tab, setTab] = useState<Tab>("inspect");
  const panelScroll = useRef<HTMLDivElement>(null);
  const tabRefs = useRef(new Map<Tab, HTMLButtonElement>());
  const scrollByTab = useRef(new Map<Tab, number>());
  const switchTab = (next: Tab) => {
    if (next === tab) return;
    if (panelScroll.current) scrollByTab.current.set(tab, panelScroll.current.scrollTop);
    setTab(next);
  };
  useLayoutEffect(() => {
    if (panelScroll.current) {
      panelScroll.current.scrollTop = scrollByTab.current.get(tab) ?? 0;
    }
  }, [tab]);
  useEffect(() => {
    const openTransition = () => switchTab("transition");
    const locateIssue = (event: Event) => {
      if ((event as LocatePreflightIssueEvent).detail.resourceKind !== "media") switchTab("inspect");
    };
    window.addEventListener("cutvoke:open-transition", openTransition);
    window.addEventListener(LOCATE_PREFLIGHT_ISSUE, locateIssue);
    return () => {
      window.removeEventListener("cutvoke:open-transition", openTransition);
      window.removeEventListener(LOCATE_PREFLIGHT_ISSUE, locateIssue);
    };
  }, [tab]);
  const hasClip = !!state.selection?.clipId;
  const hasTrack = !!state.selection?.trackId && !state.selection?.clipId;

  const tabs: { id: Tab; label: string; icon: React.ReactNode; always?: boolean }[] = [
    { id: "inspect", label: hasClip ? "片段属性" : hasTrack ? "轨道属性" : "工程信息", icon: <SlidersHorizontal size={13} /> },
    { id: "speed", label: "变速", icon: <Gauge size={13} /> },
    { id: "transition", label: "转场", icon: <ArrowRightLeft size={13} /> },
    { id: "stack", label: "效果条", icon: <ListOrdered size={13} />, always: true },
    { id: "audio", label: "音频分析", icon: <AudioLines size={13} />, always: true },
  ];
  const onTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (index - 1 + tabs.length) % tabs.length;
    } else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = tabs.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = tabs[nextIndex].id;
    switchTab(next);
    requestAnimationFrame(() => tabRefs.current.get(next)?.focus());
  };

  return (
    <div className="zone-right__inner">
      <div className="right-tabs" role="tablist" aria-label="工程编辑面板">
        {tabs.map((t, index) => (
          <button
            key={t.id}
            type="button"
            id={`${t.id}-tab`}
            role="tab"
            aria-selected={tab === t.id}
            aria-controls="right-panel-content"
            tabIndex={tab === t.id ? 0 : -1}
            ref={(element) => {
              if (element) tabRefs.current.set(t.id, element);
              else tabRefs.current.delete(t.id);
            }}
            title={t.label}
            className={`right-tabs__btn ${tab === t.id ? "right-tabs__btn--active" : ""}`}
            onKeyDown={(event) => onTabKeyDown(event, index)}
            onClick={() => switchTab(t.id)}
          >
            {t.icon}
            <span className="right-tabs__label">{t.label}</span>
          </button>
        ))}
      </div>
      <div ref={panelScroll} className="zone-right__scroll" id="right-panel-content" role="tabpanel" aria-labelledby={`${tab}-tab`} tabIndex={0}>
        {tab === "inspect" ? <Inspector /> : null}
        {tab === "speed" ? <SpeedPanel /> : null}
        {tab === "transition" ? <TransitionPanel /> : null}
        {tab === "stack" ? <EffectStackPanel active /> : null}
        {tab === "audio" ? <AudioAnalyzePanel /> : null}
      </div>
    </div>
  );
}
