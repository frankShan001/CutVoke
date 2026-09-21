/** 顶部工具栏：品牌标识 + 连接状态 + 同步状态。 */

import { Undo2, Redo2, RefreshCw, Radio, Activity, LayoutTemplate } from "lucide-react";
import { Button } from "./ui";
import { useEditor } from "../store/editor";
import { loadProjects, redo, refreshProject, undo } from "../store/actions";
import { ToolbarTools } from "./ToolbarTools";

export function TopToolbar() {
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
      <span className="topbar__tag">本地 · 面向 Agent</span>
      <ToolbarTools />
      <Button
        variant="ghost"
        size="sm"
        onClick={openTemplates}
        aria-label="打开模板库"
        title="模板库：浏览工程模板并一键套用"
      >
        <LayoutTemplate size={14} />
        模板库
      </Button>
      <span className="topbar__spacer" />
      <Button
        variant="ghost"
        size="sm"
        onClick={toggleAgentPanel}
        aria-label="打开Agent活动"
        title="Agent 活动（查看 Agent 与人工操作历史）"
        className={state.agentPanelOpen ? "topbar__agent-btn--on" : ""}
      >
        <Activity size={14} />
        Agent 活动
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleUndo}
        disabled={state.undoBlocked || !state.currentId}
        title={state.undoBlocked ? (state.historyHint || "撤销不可用") : "撤销"}
      >
        <Undo2 size={14} />
        {state.undoBlocked ? "暂无可撤销" : "撤销"}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleRedo}
        disabled={state.redoBlocked || !state.currentId}
        title={state.redoBlocked ? "重做不可用" : "重做"}
      >
        <Redo2 size={14} />
        重做
      </Button>
      <Button variant="ghost" size="sm" onClick={handleRefresh} title="刷新工程列表与当前工程">
        <RefreshCw size={14} />
        刷新
      </Button>
      <span className="topbar__sync">
        <Radio size={13} />
        <span className={`dot ${state.serverUp ? "dot--up" : "dot--down"}`} />
        {state.serverUp ? "后端已连接" : "后端未连接"}
        <span className="topbar__sync">{syncText}</span>
      </span>
    </header>
  );
}