import { Component, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "./ui";

/** A stale lazy chunk or a render failure must leave a recovery action. */
export class WorkspaceBoundary extends Component<{ children: ReactNode; onFailure?: () => void }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch() {
    this.props.onFailure?.();
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="workspace workspace-recovery" role="alert">
        <AlertTriangle size={28} />
        <h2>编辑器加载失败</h2>
        <p>连接中断或页面更新可能导致加载失败。重新加载后，可从工程列表继续编辑已保存的工程。</p>
        <Button variant="primary" onClick={() => window.location.reload()}>
          <RefreshCw size={14} /> 重新加载编辑器
        </Button>
      </main>
    );
  }
}
