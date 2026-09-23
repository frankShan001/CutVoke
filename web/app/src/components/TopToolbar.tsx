/** 顶部工具栏：品牌标识 + 连接状态 + 同步状态。 */

import {
  Undo2,
  Redo2,
  RefreshCw,
  Radio,
  Activity,
  LayoutTemplate,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";
import { Button } from "./ui";
import { useEditor } from "../store/editor";
import { loadProjects, redo, refreshProject, undo } from "../store/actions";
import { ToolbarTools } from "./ToolbarTools";

export function TopToolbar({
  leftPanelOpen,
  rightPanelOpen,
  onToggleLeftPanel,
  onToggleRightPanel,
}: {
  leftPanelOpen: boolean;
  rightPanelOpen: boolean;
  onToggleLeftPanel: () => void;
  onToggleRightPanel: () => void;
}) {
  const { state, dispatch } = useEditor();

  const syncText = state.lastSync
    ? `上次同步 ${new Date(state.lastSync).toLocaleTimeString("zh-CN", { hour12: false })}`
    : "未同步";

  const handleUndo = () => {
    void undo(dispatch, state);
  };
  const handleRedo = () => {
    void redo(dispatch, state);
  };
  const handleRefresh = () => {
    void loadProjects(dispatch);
    if (state.currentId) void refreshProject(dispatch, state.currentId);
  };
  const toggleAgentPanel = () => {
    dispatch({ type: "AGENT_PANEL_OPEN_SET", open: !state.agentPanelOpen });
  };
  const openTemplates = () => {
    dispatch({ type: "TEMPLATE_OPEN_SET", open: true });
  };

  return (
    <header className="topbar">
      <h1 className="topbar__title">CutVoke 编辑器</h1>
      <span className="topbar__tag">本地 · 人工与 Agent 共用</span>
      <ToolbarTools />
      <Button
        variant="ghost"
        size="sm"
        onClick={onToggleLeftPanel}
        aria-label={leftPanelOpen ? "隐藏素材面板" : "显示素材面板"}
        aria-pressed={leftPanelOpen}
        title={leftPanelOpen ? "隐藏左侧工程与素材面板" : "显示左侧工程与素材面板"}
      >
        {leftPanelOpen ? <PanelLeftClose size={14} /> : <PanelLeftOpen size={14} />}
        <span className="topbar__btn-label">素材</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={onToggleRightPanel}
        aria-label={rightPanelOpen ? "隐藏属性面板" : "显示属性面板"}
        aria-pressed={rightPanelOpen}
        title={rightPanelOpen ? "隐藏右侧属性面板" : "显示右侧属性面板"}
      >
        {rightPanelOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
        <span className="topbar__btn-label">属性</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={openTemplates}
        aria-label="打开模板库"
        title="模板库：浏览工程模板并一键套用"
      >
        <LayoutTemplate size={14} />
        <span className="topbar__btn-label">模板库</span>
      </Button>
      <span className="topbar__spacer" />
      <Button
        variant="ghost"
        size="sm"
        onClick={toggleAgentPanel}
        aria-label="Agent活动面板"
        aria-pressed={state.agentPanelOpen}
        title={state.agentPanelOpen ? "关闭 Agent 活动面板" : "查看 Agent 与人工操作历史"}
        className={state.agentPanelOpen ? "topbar__agent-btn--on" : ""}
      >
        <Activity size={14} />
        <span className="topbar__btn-label">Agent 活动</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleUndo}
        disabled={state.undoBlocked || !state.currentId}
        aria-label={state.undoBlocked ? "暂无可撤销" : "撤销"}
        title={state.undoBlocked ? (state.historyHint || "撤销不可用") : "撤销（Ctrl/Cmd+Z）"}
        aria-keyshortcuts="Control+Z Meta+Z"
      >
        <Undo2 size={14} />
        <span className="topbar__btn-label">{state.undoBlocked ? "暂无可撤销" : "撤销"}</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleRedo}
        disabled={state.redoBlocked || !state.currentId}
        aria-label="重做"
        title={state.redoBlocked ? "重做不可用" : "重做（Ctrl/Cmd+Shift+Z 或 Ctrl+Y）"}
        aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z Control+Y"
      >
        <Redo2 size={14} />
        <span className="topbar__btn-label">重做</span>
      </Button>
      <Button variant="ghost" size="sm" onClick={handleRefresh} aria-label="刷新" title="刷新工程列表与当前工程">
        <RefreshCw size={14} />
        <span className="topbar__btn-label">刷新</span>
      </Button>
      <span
        className="topbar__sync"
        role="status"
        aria-label={state.serverUp ? "后端已连接" : "后端未连接"}
      >
        <Radio size={13} />
        <span className={`dot ${state.serverUp ? "dot--up" : "dot--down"}`} />
        <span className="topbar__sync-label">{state.serverUp ? "后端已连接" : "后端未连接"}</span>
        <span className="topbar__sync-time">{syncText}</span>
      </span>
    </header>
  );
}
