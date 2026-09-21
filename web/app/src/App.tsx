/** CutVoke 编辑器 应用装配层。
    顶层按 store.view 切换两个视图（不用 history 路由，vite.config.ts:10）：
      home   → 工程首页（最近工程 / 新建 / 重命名）
      editor → 剪映式四区工作区（顶部工具栏 / 左素材 / 中预览+时间线 / 右属性 / 状态栏）。 */

import { useEffect, useRef } from "react";
import { EditorProvider, useEditor } from "./store/editor";
import { EditorBridge } from "./components/EditorBridge";
import { TopToolbar } from "./components/TopToolbar";
import { StatusBar } from "./components/StatusBar";
import { ErrorModal } from "./components/ErrorModal";
import { HomeView } from "./components/HomeView";
import { ProjectPanel } from "./components/ProjectPanel";
import { TemplateGallery } from "./components/TemplateGallery";
import { MediaPanel } from "./components/MediaPanel";
import { ExportDialog } from "./components/ExportDialog";
import { Player } from "./components/Player";
import { Timeline } from "./components/timeline/Timeline";
import { TimelineResizer } from "./components/TimelineResizer";
import { RightPanel } from "./components/RightPanel";
import { AgentActivityPanel } from "./components/AgentActivityPanel";

function Workspace() {
  const centerRef = useRef<HTMLDivElement | null>(null);
  return (
    <div className="workspace">
      {/* 左：当前工程 + 素材区 */}
      <aside className="zone-left">
        <div className="zone-left__scroll">
          <ProjectPanel />
          <MediaPanel />
        </div>
      </aside>

      {/* 中：预览 + 时间线 */}
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

      {/* 右：属性（上下文切换） */}
      <aside className="zone-right">
        <RightPanel />
      </aside>
    </div>
  );
}

function AppShell() {
  const { state } = useEditor();

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

  if (state.view === "home") {
    return (
      <div className="app-shell">
        <HomeView />
        <ErrorModal />
        {state.templateOpen ? <TemplateGallery /> : null}
      </div>
    );
  }
  return (
    <div className="app-shell">
      <TopToolbar />
      <Workspace />
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
