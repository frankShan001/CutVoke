/** 底部状态栏：当前工程名 / 保存状态 / 提示消息 / 轨道片段统计。
    raw projectId 与 revision 收进默认收起的「详情」，主区只留人能懂的。 */

import { useEditor, type SyncState } from "../store/editor";

function saveLabel(syncState: SyncState, lastSync: number): string {
  switch (syncState) {
    case "saving":
      return "保存中…";
    case "error":
      return "保存失败";
    case "saved":
      return lastSync
        ? `已保存 ${new Date(lastSync).toLocaleTimeString("zh-CN", { hour12: false })}`
        : "已保存";
    default:
      return "尚未加载工程";
  }
}

export function StatusBar() {
  const { state } = useEditor();
  const tracks = state.project?.sequence.tracks || [];
  const clipCount = tracks.reduce((n, t) => n + (t.clips?.length || 0), 0);
  const name = state.projectName || state.currentId || "未选择工程";

  return (
    <footer className="statusbar">
      <span className="statusbar__rev">
        当前工程 <b>{name}</b>
      </span>
      <span className={`statusbar__save statusbar__save--${state.syncState}`}>
        {saveLabel(state.syncState, state.lastSync)}
      </span>
      <span className="statusbar__rev">
        轨道 <b>{tracks.length}</b> · 片段 <b>{clipCount}</b>
      </span>
      <span className={`statusbar__msg statusbar__msg--${state.status.severity}`}>
        {state.status.text}
      </span>
      <details className="statusbar__details">
        <summary>详情</summary>
        <div className="statusbar__details-body">
          <span>
            工程 ID：<code className="cv-mono">{state.currentId || "—"}</code>
          </span>
          <span>
            Revision：<code className="cv-mono">{state.revision || "—"}</code>
          </span>
        </div>
      </details>
    </footer>
  );
}
