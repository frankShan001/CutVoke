import { useRef } from "react";
import { ProjectPanel } from "./ProjectPanel";
import { MediaPanel } from "./MediaPanel";
import { Player } from "./Player";
import { Timeline } from "./timeline/Timeline";
import { TimelineResizer } from "./TimelineResizer";
import { RightPanel } from "./RightPanel";

export default function Workspace({
  leftPanelOpen,
  rightPanelOpen,
}: {
  leftPanelOpen: boolean;
  rightPanelOpen: boolean;
}) {
  const centerRef = useRef<HTMLDivElement | null>(null);
  return (
    <div className="workspace">
      <aside className={`zone-left${leftPanelOpen ? "" : " zone-left--collapsed"}`}>
        <div className="zone-left__scroll">
          <ProjectPanel />
          <MediaPanel />
        </div>
      </aside>

      <main className="zone-center" ref={centerRef}>
        <div className="zone-center__top">
          <div className="zone-center__preview">
            <Player />
          </div>
        </div>
        <TimelineResizer containerRef={centerRef} />
        <div className="zone-center__timeline">
          <Timeline />
        </div>
      </main>

      <aside className={`zone-right${rightPanelOpen ? "" : " zone-right--collapsed"}`}>
        <RightPanel />
      </aside>
    </div>
  );
}
