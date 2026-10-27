import { useRef, useState } from "react";
import { tr } from "../i18n";
import { currentWorkspace, rememberWorkspace } from "../lib/workspaces";

export function PanelResizer({ side }: { side: "left" | "right" }) {
  const [width, setWidth] = useState(side === "left" ? currentWorkspace().leftWidth : currentWorkspace().rightWidth);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const variable = side === "left" ? "--zone-left-w" : "--zone-right-w";
  function update(value: number) {
    const maximum = Math.min(side === "left" ? 520 : 560, Math.max(240, window.innerWidth * .4));
    const next = Math.round(Math.max(side === "left" ? 200 : 240, Math.min(maximum, value)));
    setWidth(next); document.documentElement.style.setProperty(variable, `${next}px`);
  }
  return <div className="panel-resizer" role="separator" aria-orientation="vertical" aria-label={side === "left" ? tr("素材面板宽度") : tr("属性面板宽度")}
    tabIndex={0} aria-valuemin={side === "left" ? 200 : 240} aria-valuemax={side === "left" ? 520 : 560} aria-valuenow={width}
    onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { x: event.clientX, width: side === "left" ? currentWorkspace().leftWidth : currentWorkspace().rightWidth }; }}
    onPointerMove={event => { if (drag.current) update(drag.current.width + (event.clientX - drag.current.x) * (side === "left" ? 1 : -1)); }}
    onPointerUp={() => { drag.current = null; rememberWorkspace(); }} onPointerCancel={() => { drag.current = null; rememberWorkspace(); }}
    onKeyDown={event => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault();
      update(event.key === "Home" ? 0 : event.key === "End" ? 560 : width + (event.key === "ArrowRight" ? 16 : -16) * (side === "left" ? 1 : -1)); rememberWorkspace(); }} />;
}
