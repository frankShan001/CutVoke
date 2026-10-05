/** CutVoke 编辑器 应用装配层。
    顶层按 store.view 切换两个视图（不用 history 路由，vite.config.ts:10）：
      home   → 工程首页（最近工程 / 新建 / 重命名）
      editor → 剪映式四区工作区（顶部工具栏 / 左素材 / 中预览+时间线 / 右属性 / 状态栏）。 */

import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { EditorProvider, useEditor } from "./store/editor";
import { EditorBridge } from "./components/EditorBridge";
import { getLatestState, redo, undo } from "./store/actions";
import { removeClip, removeEffect } from "./store/clipEdit";
import { TopToolbar } from "./components/TopToolbar";
import { StatusBar } from "./components/StatusBar";
import { ErrorModal } from "./components/ErrorModal";
import { HomeView } from "./components/HomeView";
import { TemplateGallery } from "./components/TemplateGallery";
import { ExportDialog } from "./components/ExportDialog";
import { AgentActivityPanel } from "./components/AgentActivityPanel";
import { WorkspaceBoundary } from "./components/WorkspaceBoundary";
import { ProjectLoading, useProjectPreparation } from "./components/ProjectLoading";
import { notifyTogglePlay } from "./hooks/useKeyboardShortcuts";
import { LOCATE_PREFLIGHT_ISSUE, type LocatePreflightIssueEvent } from "./lib/preflight";

const Workspace = lazy(() => import("./components/Workspace"));

type PanelMode = "wide" | "medium" | "compact";

function panelModeForWidth(width: number): PanelMode {
  if (width <= 900) return "compact";
  // 中等桌面宽度优先给预览和时间线留空间，素材面板可随时从工具栏打开。
  if (width <= 1280) return "medium";
  return "wide";
}

const SHORTCUT_IGNORE_SELECTOR = [
  "input",
  "textarea",
  "select",
  "[role='textbox']",
  "[contenteditable]:not([contenteditable='false'])",
  "[role='dialog']",
  "[role='alertdialog']",
].join(", ");

function AppShell() {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const preparation = useProjectPreparation();
  const shortcutStateRef = useRef(state);
  shortcutStateRef.current = state;
  const [leftPanelOpen, setLeftPanelOpen] = useState(() =>
    typeof window === "undefined" || panelModeForWidth(window.innerWidth) === "wide",
  );
  const [rightPanelOpen, setRightPanelOpen] = useState(() =>
    typeof window === "undefined" || panelModeForWidth(window.innerWidth) !== "compact",
  );
  const panelPreferencesRef = useRef({ left: true, right: true });

  useEffect(() => {
    const locate = (event: Event) => {
      const issue = (event as LocatePreflightIssueEvent).detail;
      if (issue.resourceKind === "media") {
        panelPreferencesRef.current.left = true;
        setLeftPanelOpen(true);
      } else {
        panelPreferencesRef.current.right = true;
        setRightPanelOpen(true);
      }
    };
    window.addEventListener(LOCATE_PREFLIGHT_ISSUE, locate);
    return () => window.removeEventListener(LOCATE_PREFLIGHT_ISSUE, locate);
  }, []);

  // 窄窗口优先留出预览与时间线；中等宽度自动收起素材库，保留属性面板。
  // 手动开关会更新偏好，窗口恢复到桌面宽度后按用户偏好还原。
  useEffect(() => {
    let currentMode = panelModeForWidth(window.innerWidth);
    const handleResize = () => {
      const nextMode = panelModeForWidth(window.innerWidth);
      if (nextMode === currentMode) return;
      currentMode = nextMode;

      if (nextMode === "wide") {
        setLeftPanelOpen(panelPreferencesRef.current.left);
        setRightPanelOpen(panelPreferencesRef.current.right);
      } else if (nextMode === "medium") {
        setLeftPanelOpen(false);
        setRightPanelOpen(panelPreferencesRef.current.right);
      } else {
        setLeftPanelOpen(false);
        setRightPanelOpen(false);
      }
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  // 全屏遮罩能拦截鼠标，但不能阻止已经聚焦的按钮响应空格/回车，或全局
  // 快捷键继续派发编辑命令。租约期间在捕获阶段统一吞掉键盘输入，避免
  // 页面“看起来只读”但仍能被键盘改写。
  useEffect(() => {
    if (!state.editLock) return;
    const blockKeyboardEditing = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
    };
    window.addEventListener("keydown", blockKeyboardEditing, true);
    return () => window.removeEventListener("keydown", blockKeyboardEditing, true);
  }, [state.editLock]);

  // 只在编辑器视图注册常用快捷键。输入框/弹窗保留原生键盘行为；
  // Ctrl/Cmd 只接管撤销/重做，其它组合键仍交给浏览器和系统。
  useEffect(() => {
    if (
      state.view !== "editor" ||
      state.opening ||
      state.editLock ||
      state.error ||
      state.exportOpen ||
      state.templateOpen ||
      state.agentPanelOpen
    ) return;
    const handleEditorShortcut = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.repeat || event.isComposing || event.altKey) return;
      const latest = shortcutStateRef.current;
      if (latest.view !== "editor" || latest.opening || latest.editLock || latest.error || latest.exportOpen || latest.templateOpen || latest.agentPanelOpen) return;

      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.isContentEditable || target.closest(SHORTCUT_IGNORE_SELECTOR)) return;

      const key = event.key.toLowerCase();
      if (event.ctrlKey || event.metaKey) {
        if (key === "z" && event.shiftKey) {
          if (latest.currentId && !latest.redoBlocked && !latest.editLock) {
            event.preventDefault();
            void redo(dispatch, latest);
          }
        } else if (key === "y" && !event.shiftKey) {
          if (latest.currentId && !latest.redoBlocked && !latest.editLock) {
            event.preventDefault();
            void redo(dispatch, latest);
          }
        } else if (key === "z" && !event.shiftKey) {
          if (latest.currentId && !latest.undoBlocked && !latest.editLock) {
            event.preventDefault();
            void undo(dispatch, latest);
          }
        }
        return;
      }
      if (event.shiftKey) return;
      if (key === " " && !target.closest("button, a, summary, [role='button'], video")) {
        event.preventDefault();
        notifyTogglePlay();
      } else if ((key === "delete" || key === "backspace") && latest.selection?.clipId) {
        const selection = latest.selection;
        const clipId = selection.clipId!;
        const track = latest.project?.sequence.tracks.find((item) => item.id === selection.trackId);
        event.preventDefault();
        if (track?.locked) {
          dispatch({ type: "STATUS_SET", severity: "warn", text: "轨道已锁定，解锁后才能删除" });
          return;
        }
        const current = getLatestState() || latest;
        const removal = selection.effectId
          ? removeEffect(dispatch, current, { clipId, effectId: selection.effectId })
          : removeClip(dispatch, current, clipId);
        void removal.then((result) => {
          if (result.ok) dispatch({ type: "SELECTION_SET", selection: null });
        });
      } else if (key === "v" || key === "c") {
        dispatch({ type: "TOOL_MODE_SET", mode: key === "v" ? "select" : "cut" });
      }
    };
    window.addEventListener("keydown", handleEditorShortcut);
    return () => window.removeEventListener("keydown", handleEditorShortcut);
  }, [
    dispatch,
    state.agentPanelOpen,
    state.editLock,
    state.error,
    state.exportOpen,
    state.templateOpen,
    state.view,
    state.opening,
  ]);

  if (state.view === "home") {
    return (
      <>
      <div className="app-shell" inert={state.opening || undefined} aria-hidden={state.opening || undefined}>
        <HomeView />
        <ErrorModal />
        {state.templateOpen ? <TemplateGallery /> : null}
      </div>
      {state.opening ? <ProjectLoading preparation={preparation} /> : null}
      </>
    );
  }
  return (
    <>
    <div className="app-shell" inert={state.opening || undefined} aria-hidden={state.opening || undefined}>
      <TopToolbar
        leftPanelOpen={leftPanelOpen}
        rightPanelOpen={rightPanelOpen}
        onToggleLeftPanel={() => setLeftPanelOpen((open) => {
          const next = !open;
          panelPreferencesRef.current.left = next;
          return next;
        })}
        onToggleRightPanel={() => setRightPanelOpen((open) => {
          const next = !open;
          panelPreferencesRef.current.right = next;
          return next;
        })}
      />
      <WorkspaceBoundary onFailure={preparation.repair}>
        <Suspense fallback={<div className="workspace" role="status">编辑器加载中…</div>}>
          <Workspace leftPanelOpen={leftPanelOpen} rightPanelOpen={rightPanelOpen} />
        </Suspense>
      </WorkspaceBoundary>
      <StatusBar />
      <ErrorModal />
      {state.exportOpen ? <ExportDialog /> : null}
      {state.templateOpen ? <TemplateGallery /> : null}
      <AgentActivityPanel />
      {state.editLock ? (
        <div className="agent-edit-lock" role="status" aria-live="polite">
          <div className="agent-edit-lock__card">
            <strong>Agent 正在编辑</strong>
            <span>{state.editLock.owner} · 页面暂时只读</span>
            <small>工程内容会继续更新，Agent 完成后页面将自动恢复编辑。</small>
          </div>
        </div>
      ) : null}
    </div>
    {state.opening ? <ProjectLoading preparation={preparation} /> : null}
    </>
  );
}

export default function App() {
  return (
    <EditorProvider>
      <EditorBridge />
      <AppShell />
    </EditorProvider>
  );
}
