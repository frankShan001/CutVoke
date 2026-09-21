/** 工具栏工具切换（剪映范式）：选择(V) / 切割(C) + 磁吸开关 + 导出。
    「导出」打开统一导出抽屉（ExportDialog），不再依赖无人监听的自定义事件。 */

import { MousePointer2, Scissors, Magnet, Upload } from "lucide-react";
import { useEditor } from "../store/editor";

export function ToolbarTools() {
  const { state, dispatch } = useEditor();
  return (
    <div className="toolbar-tools">
      <button
        className={`tool-btn ${state.toolMode === "select" ? "tool-btn--on" : ""}`}
        onClick={() => dispatch({ type: "TOOL_MODE_SET", mode: "select" })}
        title="选择工具 (V)"
        aria-label="选择工具"
      >
        <MousePointer2 size={14} />
        <span className="tool-btn__key">V</span>
      </button>
      <button
        className={`tool-btn ${state.toolMode === "cut" ? "tool-btn--on" : ""}`}
        onClick={() => dispatch({ type: "TOOL_MODE_SET", mode: "cut" })}
        title="切割工具 (C)：点击时间线在播放头分割"
        aria-label="切割工具"
      >
        <Scissors size={14} />
        <span className="tool-btn__key">C</span>
      </button>
      <span className="toolbar-tools__sep" />
      <button
        className={`tool-btn ${state.snapEnabled ? "tool-btn--on" : ""}`}
        onClick={() => dispatch({ type: "SNAP_TOGGLE" })}
        title="磁吸吸附（片段边缘/播放头）"
        aria-label="磁吸开关"
      >
        <Magnet size={14} />
      </button>
      <span className="toolbar-tools__sep" />
      <button
        className="tool-btn tool-btn--export"
        onClick={() => dispatch({ type: "EXPORT_OPEN_SET", open: true })}
        title="导出成片"
        aria-label="导出"
      >
        <Upload size={14} />
        导出
      </button>
    </div>
  );
}
