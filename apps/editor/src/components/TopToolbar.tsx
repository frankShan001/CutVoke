import { tr, useLocale } from "../i18n";
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
  Search,
  Layers3,
  Box,
} from "lucide-react";
import { Button } from "./ui";
import { useEditor } from "../store/editor";
import { getLatestState, loadProjects, redo, refreshProject, runCommand, undo } from "../store/actions";
import { ToolbarTools } from "./ToolbarTools";
import { CacheSettings } from "./CacheSettings";
import { LanguageSelect } from "./LanguageSelect";
import { RuntimeStatus } from "./RuntimeStatus";
import { WorkspaceSettings } from "./WorkspaceSettings";

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
  const locale = useLocale();
  const { state, dispatch } = useEditor();

  const syncText = state.lastSync
    ? tr("上次同步 {0}", new Date(state.lastSync).toLocaleTimeString(locale, { hour12: false }))
    : tr("未同步");

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
  const addScene=async()=>{
    const owner=state.currentId,clipId=`scene_${crypto.randomUUID().slice(0,12)}`;
    const result=await runCommand(dispatch,state,'composition.scene3d',{clipId,timelineStart:state.playhead,timelineEnd:state.playhead+3});
    if(result.ok && getLatestState()?.currentId===owner)dispatch({type:'CLIP_SELECTION_SET',clipIds:[clipId],primaryId:clipId,anchorId:clipId});
  };

  return (
    <header className="topbar">
      <h1 className="topbar__title"><img src="/brand/icon-64.png" width="24" height="24" alt="" aria-hidden="true" draggable={false} />{tr("CutVoke 编辑器")}</h1>
      <span className="topbar__tag">{tr("本地 · 人工与 Agent 共用")}</span>
      <ToolbarTools />
      <WorkspaceSettings />
      <Button variant="ghost" size="sm" disabled={!state.project || !!state.editLock} aria-label={tr('添加三维场景')}
        onClick={()=>void addScene()}>
        <Box size={14}/><span className="topbar__btn-label">{tr('添加三维场景')}</span></Button>
      <Button variant="ghost" size="sm" disabled={!state.project || !!state.editLock} aria-label={tr("添加调整层")}
        onClick={() => void runCommand(dispatch, state, "composition.adjustment", { timelineStart: state.playhead,
          timelineEnd: Math.max(state.playhead + 3, ...state.project!.sequence.tracks.flatMap(track => track.clips.map(clip => Number(clip.timelineEnd.num) / Number(clip.timelineEnd.den)))) })}>
        <Layers3 size={14} /><span className="topbar__btn-label">{tr("添加调整层")}</span>
      </Button>
      <Button variant="ghost" size="sm" disabled={!!state.editLock} onClick={()=>window.dispatchEvent(new CustomEvent('cutvoke:quick-apply'))}
        aria-label={tr('快速应用')} title={tr('搜索特效、预设和命令（Ctrl/⌘+Enter）')}><Search size={14}/><span className="topbar__btn-label">{tr('快速应用')}</span></Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={onToggleLeftPanel}
        aria-label={leftPanelOpen ? tr("隐藏素材面板") : tr("显示素材面板")}
        aria-pressed={leftPanelOpen}
        title={leftPanelOpen ? tr("隐藏左侧工程与素材面板") : tr("显示左侧工程与素材面板")}
      >
        {leftPanelOpen ? <PanelLeftClose size={14} /> : <PanelLeftOpen size={14} />}
        <span className="topbar__btn-label">{tr("素材")}</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={onToggleRightPanel}
        aria-label={rightPanelOpen ? tr("隐藏属性面板") : tr("显示属性面板")}
        aria-pressed={rightPanelOpen}
        title={rightPanelOpen ? tr("隐藏右侧属性面板") : tr("显示右侧属性面板")}
      >
        {rightPanelOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
        <span className="topbar__btn-label">{tr("属性")}</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={openTemplates}
        aria-label={tr("打开模板库")}
        title={tr("模板库：浏览工程模板并一键套用")}
      >
        <LayoutTemplate size={14} />
        <span className="topbar__btn-label">{tr("模板库")}</span>
      </Button>
      <span className="topbar__spacer" />
      <RuntimeStatus />
      <CacheSettings />
      <LanguageSelect />
      <Button
        variant="ghost"
        size="sm"
        onClick={toggleAgentPanel}
        aria-label={tr("Agent活动面板")}
        aria-pressed={state.agentPanelOpen}
        title={state.agentPanelOpen ? tr("关闭 Agent 活动面板") : tr("查看 Agent 与人工操作历史")}
        className={state.agentPanelOpen ? "topbar__agent-btn--on" : ""}
      >
        <Activity size={14} />
        <span className="topbar__btn-label">{tr("Agent 活动")}</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleUndo}
        disabled={state.undoBlocked || !state.currentId}
        aria-label={state.undoBlocked ? tr("暂无可撤销") : tr("撤销")}
        title={state.undoBlocked ? (state.historyHint || tr("撤销不可用")) : tr("撤销（Ctrl/Cmd+Z）")}
        aria-keyshortcuts="Control+Z Meta+Z"
      >
        <Undo2 size={14} />
        <span className="topbar__btn-label">{state.undoBlocked ? tr("暂无可撤销") : tr("撤销")}</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleRedo}
        disabled={state.redoBlocked || !state.currentId}
        aria-label={tr("重做")}
        title={state.redoBlocked ? tr("重做不可用") : tr("重做（Ctrl/Cmd+Shift+Z 或 Ctrl+Y）")}
        aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z Control+Y"
      >
        <Redo2 size={14} />
        <span className="topbar__btn-label">{tr("重做")}</span>
      </Button>
      <Button variant="ghost" size="sm" onClick={handleRefresh} aria-label={tr("刷新")} title={tr("刷新工程列表与当前工程")}>
        <RefreshCw size={14} />
        <span className="topbar__btn-label">{tr("刷新")}</span>
      </Button>
      <span
        className="topbar__sync"
        role="status"
        aria-label={state.serverUp ? tr("后端已连接") : tr("后端未连接")}
      >
        <Radio size={13} />
        <span className={`dot ${state.serverUp ? "dot--up" : "dot--down"}`} />
        <span className="topbar__sync-label">{state.serverUp ? tr("后端已连接") : tr("后端未连接")}</span>
        <span className="topbar__sync-time">{syncText}</span>
      </span>
    </header>
  );
}
